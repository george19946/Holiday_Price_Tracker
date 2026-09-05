"""HTTP plumbing shared by the real providers: a per-host token-bucket rate
limiter, a SQLite-backed response cache with per-call TTLs, and retry with
exponential backoff on 429/5xx.

This is the one place that has to respect the free-tier rate limits the
project plan is built around (Travelpayouts flights: 300 req/min;
Hotellook stays: 60 req/min) — a wide regional scan must not be able to
burn through or get locked out of either quota. Providers should route
every request through CachedHttpClient.get_json rather than calling httpx
directly, so caching, rate-limiting, and retry behaviour stay centralised
and easy to reason about in one place.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx


class ProviderError(RuntimeError):
    """Raised when a provider request ultimately fails after retries.

    Deliberately a plain RuntimeError subclass (not per-status-code
    subclasses): callers care whether the run should carry on with
    partial data or abort, not the precise HTTP mechanics of why one
    request failed.
    """


@dataclass
class TokenBucket:
    """A token bucket rate limiter: `capacity` tokens, refilled continuously
    at `rate_per_minute` tokens/minute.

    `clock` and `sleep` are injectable so tests can simulate the passage of
    time instantly instead of actually waiting — see tests/test_http.py.
    """

    rate_per_minute: float
    capacity: float | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _tokens: float = field(init=False, repr=False)
    _last_refill: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.capacity is None:
            self.capacity = self.rate_per_minute
        self._tokens = self.capacity
        self._last_refill = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self.capacity, self._tokens + elapsed * (self.rate_per_minute / 60.0))
        self._last_refill = now

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        self._refill()
        if self._tokens < 1:
            deficit = 1 - self._tokens
            wait_seconds = deficit / (self.rate_per_minute / 60.0)
            self.sleep(wait_seconds)
            self._refill()
        self._tokens -= 1


class ResponseCache:
    """A small SQLite-backed cache for provider HTTP responses, keyed by a
    caller-supplied string with a per-entry TTL.

    Good enough for a single-process CLI; not intended for concurrent
    multi-process use. Pass ":memory:" (the default) for tests or one-off
    runs, or a real path to persist the cache across CLI invocations (the
    whole point for local development: re-running a search against
    yesterday's cache costs zero requests).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS http_cache (
                key TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                cached_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, key: str, *, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        row = self._conn.execute(
            "SELECT body, expires_at FROM http_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        body, expires_at = row
        if now >= expires_at:
            return None
        return body

    def set(self, key: str, body: str, ttl_seconds: float, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._conn.execute(
            "INSERT OR REPLACE INTO http_cache (key, body, cached_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (key, body, now, now + ttl_seconds),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _default_cache_key(url: str, params: dict[str, object] | None) -> str:
    normalized_params = json.dumps(params or {}, sort_keys=True, default=str)
    return f"{url}?{normalized_params}"


class CachedHttpClient:
    """A GET-only JSON HTTP client with per-host rate limiting, response
    caching, and retry-with-backoff, shared by every real provider.

    `client` accepts an httpx.Client so tests can inject one built on
    httpx.MockTransport instead of hitting the network.
    """

    def __init__(
        self,
        cache: ResponseCache,
        limiters: dict[str, TokenBucket] | None = None,
        *,
        client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache = cache
        self._limiters = limiters or {}
        self._client = client or httpx.Client(timeout=15.0)
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        # Requests actually sent to the network (cache hits don't count) --
        # the search engine uses this to report how much of the free-tier
        # quota a run actually spent.
        self.request_count = 0

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        cache_ttl_seconds: float,
        cache_key: str | None = None,
    ) -> object:
        key = cache_key or _default_cache_key(url, params)
        cached = self._cache.get(key)
        if cached is not None:
            return json.loads(cached)

        host = urlparse(url).netloc
        limiter = self._limiters.get(host)

        last_error: Exception | None = None
        for attempt in range(self._retry_policy.max_retries + 1):
            if limiter is not None:
                limiter.acquire()
            self.request_count += 1
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_error = exc
                self._sleep(self._retry_policy.base_delay_seconds * (2**attempt))
                continue

            if response.status_code in self._retry_policy.retry_statuses:
                last_error = ProviderError(
                    f"{url} returned {response.status_code}: {response.text[:200]}"
                )
                self._sleep(self._retry_policy.base_delay_seconds * (2**attempt))
                continue

            if response.status_code >= 400:
                raise ProviderError(
                    f"{url} returned {response.status_code}: {response.text[:200]}"
                )

            self._cache.set(key, response.text, cache_ttl_seconds)
            return response.json()

        raise ProviderError(
            f"{url} failed after {self._retry_policy.max_retries + 1} attempt(s)"
        ) from last_error

    def close(self) -> None:
        self._client.close()
