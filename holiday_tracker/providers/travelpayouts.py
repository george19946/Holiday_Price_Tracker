"""Adapter for the Travelpayouts / Aviasales flight-price Data API.

Free, token-gated sign-up at https://www.travelpayouts.com/ — see the
project README for setup. Prices are Aviasales' *cached* search results,
not a live GDS fare quote (there is currently no free live flight-price API
for independent developers; Amadeus's free self-service tier shut down in
mid-2026). Every quote this adapter produces should be shown to the user
with its observed_at timestamp and treated as indicative, not bookable.

The calendar endpoint (`/v1/prices/calendar`) is what makes a wide scan
affordable: it returns the cheapest fare for every day of a month in a
single request, which is why the search engine (phase 3) sweeps flights
month-by-month rather than day-by-day — "any Thursday in 2027" costs 12
requests, not 52.

Rate limit: 300 requests/minute (see providers/http.py's TokenBucket).

VERIFICATION NOTE: this adapter is written against Travelpayouts' published
documentation (https://travelpayouts-data-api.readthedocs.io/ and
https://travelpayouts.github.io/slate/) but has not been exercised against
a live token in this environment — no token is available here. Before
relying on it for a real search, run `holiday-track init` with
TRAVELPAYOUTS_TOKEN set and compare one fare_calendar() call's output to
the raw JSON response, in particular:
  - that `data` is a mapping of "YYYY-MM-DD" -> {"price": ...} (assumed
    below) rather than a list;
  - what a departure day with no cached fare looks like (assumed: either
    the key is absent or its value is null/empty — both are handled).
If the real shape differs, only this file and its tests need to change —
the FlightProvider protocol and everything downstream is unaffected.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from holiday_tracker.models import Money
from holiday_tracker.providers.base import FareCalendar
from holiday_tracker.providers.http import CachedHttpClient

CALENDAR_URL = "https://api.travelpayouts.com/v1/prices/calendar"

# The calendar endpoint returns fares Aviasales has recently seen and
# cached, not a live quote; a half-day TTL keeps repeated runs and local
# development cheap while still refreshing often enough for a daily-cron
# watch to notice real price movement.
CALENDAR_CACHE_TTL_SECONDS = 12 * 60 * 60


class TravelpayoutsFlightProvider:
    """FlightProvider backed by Aviasales' cached fare calendar."""

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

    def fare_calendar(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> FareCalendar:
        depart_month = f"{year:04d}-{month:02d}"
        payload = self._http.get_json(
            CALENDAR_URL,
            params={
                "origin": origin,
                "destination": destination_iata,
                "depart_date": depart_month,
                "calendar_type": "departure_date",
                "currency": self._currency,
            },
            headers={"X-Access-Token": self._token},
            cache_ttl_seconds=CALENDAR_CACHE_TTL_SECONDS,
            cache_key=f"calendar:{origin}:{destination_iata}:{depart_month}:{self._currency}",
        )

        prices: dict[date, Money] = {}
        data = payload.get("data") if isinstance(payload, dict) else None
        for day_str, entry in (data or {}).items():
            price = entry.get("price") if isinstance(entry, dict) else entry
            if price is None:
                continue
            try:
                depart = date.fromisoformat(day_str)
            except ValueError:
                continue
            prices[depart] = Money.from_major(float(price), self._currency.upper())

        return FareCalendar(
            origin=origin,
            destination_iata=destination_iata,
            year=year,
            month=month,
            prices=prices,
            observed_at=datetime.now(UTC),
            source="travelpayouts",
        )
