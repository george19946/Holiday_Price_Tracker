"""Protocols the search engine (phase 3) programs against, so `--provider
fixtures` (tests, CI, demos) and the real Travelpayouts/Hotellook adapters
are interchangeable.

FlightProvider's shape reflects what live testing against a real
Travelpayouts token (2026-09-05) actually confirmed the free API returns,
not the "one clean grid of daily prices" the docs summary implied: a
fare-calendar request hands back whichever concrete, already-priced
round-trip fares the provider happens to have cached for a route (a
sparse set spanning a rolling window, not a specific month), and a
month-scoped lookup hands back at most the single cheapest fare found
for that month. Neither gives "the price for this exact date I chose" --
the engine (phase 3) has to check each real fare it gets back against the
DateRule instead of generating dates and pricing them (see
dates.matches_date_rule). See providers/travelpayouts.py's module
docstring for the full evidence trail.

StayProvider returns a small shortlist per call because stay pricing is
only ever run against the narrowed-down candidates from stage 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from holiday_tracker.models import Money, StayQuote


@dataclass(frozen=True)
class CalendarFare:
    """One concrete, already-priced round-trip fare a provider has
    cached: a specific depart/return date pair, not a price we get to
    pair with a return date of our own choosing."""

    depart_date: date
    return_date: date
    price: Money
    observed_at: datetime
    source: str
    deep_link: str | None = None


class FlightProvider(Protocol):
    """Fetches whatever cached round-trip fares a provider currently has
    for one route, plus a per-month cheapest-fare fallback."""

    def fare_calendar(self, origin: str, destination_iata: str) -> list[CalendarFare]:
        """A route's full set of currently-cached fares, one call
        regardless of dates -- cheap, but sparse and not month-scoped."""
        ...

    def cheapest_fare_in_month(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> CalendarFare | None:
        """The single cheapest fare found for departures around that
        month, or None if the provider has nothing to offer -- a
        genuinely month-scoped signal that catches a fare the sparse
        fare_calendar() sweep missed, at the cost of one request per
        month actually checked."""
        ...


class StayProvider(Protocol):
    """Fetches a shortlist of candidate stays for one city and date range.

    Returns quotes unfiltered and roughly cheapest-first; applying
    StayFilters (no hostels, min rating, etc.) is the engine's job
    (engine/filters.py), not the provider's — the provider's only
    responsibility is reporting what it observed.
    """

    def search(
        self, city_id: str, check_in: date, check_out: date, adults: int, limit: int = 20
    ) -> list[StayQuote]: ...
