"""Protocols the search engine (phase 3) programs against, so `--provider
fixtures` (tests, CI, demos) and the real Travelpayouts/Hotellook adapters
are interchangeable.

Keeping these narrow is what makes the two-stage funnel described in the
project plan possible: FlightProvider is deliberately calendar-shaped (one
call = a whole month of daily fares) because that's what keeps a wide
regional sweep affordable on a free-tier rate limit, while StayProvider
returns a small shortlist per call because stay pricing is only ever run
against the narrowed-down candidates from stage 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from holiday_tracker.models import Money, StayQuote


@dataclass(frozen=True)
class FareCalendar:
    """The result of one fare-calendar request: the cheapest fare found for
    each day of a single month, for one origin/destination pair.

    Deliberately holds prices rather than full FlightQuote objects — a
    calendar entry only pins down a *departure* day; which return day it was
    priced against is a per-provider detail (see travelpayouts.py), and the
    engine (phase 3) is the one that knows which return date a given
    DateRule actually wants.
    """

    origin: str
    destination_iata: str
    year: int
    month: int
    prices: dict[date, Money]  # depart_date -> cheapest fare found
    observed_at: datetime
    source: str


class FlightProvider(Protocol):
    """Fetches a month's worth of cheapest daily fares in one call."""

    def fare_calendar(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> FareCalendar: ...


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
