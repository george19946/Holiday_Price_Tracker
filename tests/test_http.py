"""Unit tests for providers/http.py: TokenBucket, ResponseCache, CachedHttpClient.

All time-dependent behaviour is driven by injectable clock/sleep functions
so these tests run instantly and deterministically -- no real waiting, no
network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from holiday_tracker.providers.http import (
    CachedHttpClient,
    ProviderError,
    ResponseCache,
    RetryPolicy,
    TokenBucket,
)


class FakeClock:
    """A manually-advanced clock for deterministic rate-limiter tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTokenBucket:
    def test_starts_full_and_allows_capacity_immediately(self):
        clock = FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(rate_per_minute=60, clock=clock, sleep=sleeps.append)
        for _ in range(60):
            bucket.acquire()
        assert sleeps == []  # never had to wait to exhaust the initial capacity

    def test_blocks_once_capacity_exhausted(self):
        clock = FakeClock()
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        bucket = TokenBucket(rate_per_minute=60, capacity=1, clock=clock, sleep=fake_sleep)
        bucket.acquire()  # consumes the one token
        bucket.acquire()  # must wait ~1 second for the next token at 60/min
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(1.0, rel=0.01)

    def test_refills_over_time_without_sleeping(self):
        clock = FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(rate_per_minute=60, capacity=1, clock=clock, sleep=sleeps.append)
        bucket.acquire()
        clock.advance(1.0)  # a full second at 60/min refills exactly one token
        bucket.acquire()
        assert sleeps == []


class TestResponseCache:
    def test_miss_then_hit(self):
        cache = ResponseCache(":memory:")
        assert cache.get("k") is None
        cache.set("k", "body", ttl_seconds=60, now=0.0)
        assert cache.get("k", now=30.0) == "body"

    def test_expires_after_ttl(self):
        cache = ResponseCache(":memory:")
        cache.set("k", "body", ttl_seconds=60, now=0.0)
        assert cache.get("k", now=61.0) is None

    def test_set_overwrites_existing_key(self):
        cache = ResponseCache(":memory:")
        cache.set("k", "first", ttl_seconds=60, now=0.0)
        cache.set("k", "second", ttl_seconds=60, now=0.0)
        assert cache.get("k", now=0.0) == "second"


def _client_with_transport(handler, **kwargs) -> CachedHttpClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return CachedHttpClient(
        cache=ResponseCache(":memory:"),
        client=http_client,
        sleep=lambda _seconds: None,
        **kwargs,
    )


class TestCachedHttpClient:
    def test_successful_get_returns_json_and_counts_the_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        client = _client_with_transport(handler)
        result = client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert result == {"ok": True}
        assert client.request_count == 1

    def test_second_call_is_served_from_cache_without_a_new_request(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"n": len(calls)})

        client = _client_with_transport(handler)
        first = client.get_json("https://example.test/api", cache_ttl_seconds=60)
        second = client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert first == second == {"n": 1}
        assert len(calls) == 1
        assert client.request_count == 1

    def test_different_params_are_cached_separately(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"q": request.url.params.get("q")})

        client = _client_with_transport(handler)
        a = client.get_json("https://example.test/api", params={"q": "a"}, cache_ttl_seconds=60)
        b = client.get_json("https://example.test/api", params={"q": "b"}, cache_ttl_seconds=60)
        assert a == {"q": "a"}
        assert b == {"q": "b"}
        assert client.request_count == 2

    def test_retries_on_429_then_succeeds(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, json={"ok": True})

        client = _client_with_transport(handler, retry_policy=RetryPolicy(max_retries=3))
        result = client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert result == {"ok": True}
        assert len(attempts) == 3

    def test_gives_up_after_max_retries_and_raises_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client_with_transport(handler, retry_policy=RetryPolicy(max_retries=2))
        with pytest.raises(ProviderError):
            client.get_json("https://example.test/api", cache_ttl_seconds=60)

    def test_client_error_raises_immediately_without_retrying(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(404, text="not found")

        client = _client_with_transport(handler, retry_policy=RetryPolicy(max_retries=3))
        with pytest.raises(ProviderError):
            client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert len(attempts) == 1

    def test_transport_error_is_retried(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"ok": True})

        client = _client_with_transport(handler, retry_policy=RetryPolicy(max_retries=2))
        result = client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert result == {"ok": True}
        assert len(attempts) == 2

    def test_rate_limiter_is_consulted_per_host(self):
        acquired = []

        class RecordingBucket:
            def acquire(self):
                acquired.append(1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport)
        client = CachedHttpClient(
            cache=ResponseCache(":memory:"),
            limiters={"example.test": RecordingBucket()},
            client=http_client,
        )
        client.get_json("https://example.test/api", cache_ttl_seconds=60)
        assert acquired == [1]

    def test_cache_key_override_is_respected(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"n": len(calls)})

        client = _client_with_transport(handler)
        client.get_json(
            "https://example.test/api", params={"a": 1}, cache_ttl_seconds=60, cache_key="fixed"
        )
        client.get_json(
            "https://example.test/api", params={"a": 2}, cache_ttl_seconds=60, cache_key="fixed"
        )
        assert len(calls) == 1  # second call hit the cache despite different params


def test_response_cache_key_json_is_stable_for_dict_ordering():
    from holiday_tracker.providers.http import _default_cache_key

    a = _default_cache_key("https://x", {"b": 1, "a": 2})
    b = _default_cache_key("https://x", {"a": 2, "b": 1})
    assert a == b
    assert json.loads(a.split("?", 1)[1]) == {"a": 2, "b": 1}
