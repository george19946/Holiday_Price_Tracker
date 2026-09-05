"""Adapter for the Travelpayouts / Aviasales flight-price Data API.

Free, token-gated sign-up at https://www.travelpayouts.com/ — see the
project README for setup. Prices are Aviasales' *cached* search results,
not a live GDS fare quote (there is currently no free live flight-price API
for independent developers; Amadeus's free self-service tier shut down in
mid-2026). Every quote this adapter produces should be shown to the user
with its observed_at timestamp and treated as indicative, not bookable.

VERIFIED BEHAVIOUR (against a live token, 2026-09-05, route LHR->BCN):
this adapter's shape was corrected after live testing contradicted the
originally assumed one -- documented here because it's surprising enough
to re-litigate if this ever needs touching again.

  - `/v1/prices/calendar`'s `depart_date` parameter does NOT scope results
    to the requested month. Four different requested months (2026-10,
    2026-12, 2027-01, 2027-03) and one specific day all returned the
    *identical* 52 entries, spanning roughly Sep 2026 - Jul 2027. In other
    words, this endpoint returns "whatever round-trip fares Aviasales
    currently has cheaply cached for this route" -- a sparse, rolling set,
    not a per-day price grid for a chosen month. Each entry already pins
    down both `departure_at` and `return_at` (i.e. a fixed nights length
    we don't get to choose) and has no booking link.
  - `/aviasales/v3/prices_for_dates` (with `one_way=false`) DOES respect
    its `departure_at=YYYY-MM` parameter -- requesting 2027-03 returned a
    real fare departing 2027-03-23 -- but only the single cheapest fare
    found for that month, not a calendar. Each entry has a `return_at` and
    a real (relative) booking `link`, which is what a fits-budget alert or
    a report can actually offer as "verify before booking".

So `fare_calendar()` is one call per route (the month parameter would be
theatre), and `cheapest_fare_in_month()` is a genuinely month-scoped
fallback for whichever months a DateRule actually spans. Neither lets the
engine ask "what does this exact date cost" -- see dates.matches_date_rule,
which is how engine/search.py checks a real fare against a DateRule
instead of generating dates and pricing them.

Rate limit: 300 requests/minute (see providers/http.py's TokenBucket).
"""

from __future__ import annotations

from datetime import UTC, datetime

from holiday_tracker.models import Money
from holiday_tracker.providers.base import CalendarFare
from holiday_tracker.providers.http import CachedHttpClient

CALENDAR_URL = "https://api.travelpayouts.com/v1/prices/calendar"
PRICES_FOR_DATES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

_AVIASALES_BASE_URL = "https://www.aviasales.com"

# The calendar endpoint's fares aren't scoped to any particular month, so
# there's no natural refresh cadence tied to a request's parameters; a
# half-day TTL keeps repeated runs and local development cheap while still
# refreshing often enough for a daily-cron watch to notice real movement.
CALENDAR_CACHE_TTL_SECONDS = 12 * 60 * 60
MONTHLY_CACHE_TTL_SECONDS = 12 * 60 * 60


def _resolve_deep_link(link: str | None) -> str | None:
    """Aviasales' `link` field is a relative path (e.g. "/search/...");
    prefix it into a URL a user can actually open. No affiliate marker is
    appended since this project has none configured -- the link still
    works, it just won't attribute the referral."""
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{_AVIASALES_BASE_URL}{link}"


class TravelpayoutsFlightProvider:
    """FlightProvider backed by Aviasales' cached fares."""

    def __init__(self, http_client: CachedHttpClient, token: str, currency: str = "gbp") -> None:
        self._http = http_client
        self._token = token
        self._currency = currency

    @property
    def request_count(self) -> int:
        """Requests actually sent to the network so far (cache hits don't
        count) -- forwarded from the underlying CachedHttpClient so a search
        run can report how much of the free-tier quota it spent."""
        return self._http.request_count

    def fare_calendar(self, origin: str, destination_iata: str) -> list[CalendarFare]:
        payload = self._http.get_json(
            CALENDAR_URL,
            params={
                "origin": origin,
                "destination": destination_iata,
                "calendar_type": "departure_date",
                "currency": self._currency,
            },
            headers={"X-Access-Token": self._token},
            cache_ttl_seconds=CALENDAR_CACHE_TTL_SECONDS,
            cache_key=f"calendar:{origin}:{destination_iata}:{self._currency}",
        )

        observed_at = datetime.now(UTC)
        fares: list[CalendarFare] = []
        data = payload.get("data") if isinstance(payload, dict) else None
        for entry in (data or {}).values():
            if not isinstance(entry, dict):
                continue
            price = entry.get("price")
            departure_at = entry.get("departure_at")
            return_at = entry.get("return_at")
            if price is None or not departure_at or not return_at:
                continue
            fares.append(
                CalendarFare(
                    depart_date=datetime.fromisoformat(departure_at).date(),
                    return_date=datetime.fromisoformat(return_at).date(),
                    price=Money.from_major(float(price), self._currency.upper()),
                    observed_at=observed_at,
                    source="travelpayouts",
                )
            )
        return fares

    def cheapest_fare_in_month(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> CalendarFare | None:
        month_key = f"{year:04d}-{month:02d}"
        payload = self._http.get_json(
            PRICES_FOR_DATES_URL,
            params={
                "origin": origin,
                "destination": destination_iata,
                "departure_at": month_key,
                "one_way": "false",
                "currency": self._currency,
                "token": self._token,
            },
            cache_ttl_seconds=MONTHLY_CACHE_TTL_SECONDS,
            cache_key=f"monthly:{origin}:{destination_iata}:{month_key}:{self._currency}",
        )

        entries = payload.get("data") if isinstance(payload, dict) else None
        if not entries:
            return None
        entry = entries[0]
        departure_at = entry.get("departure_at")
        return_at = entry.get("return_at")
        price = entry.get("price")
        if not departure_at or not return_at or price is None:
            return None

        return CalendarFare(
            depart_date=datetime.fromisoformat(departure_at).date(),
            return_date=datetime.fromisoformat(return_at).date(),
            price=Money.from_major(float(price), self._currency.upper()),
            observed_at=datetime.now(UTC),
            source="travelpayouts",
            deep_link=_resolve_deep_link(entry.get("link")),
        )
