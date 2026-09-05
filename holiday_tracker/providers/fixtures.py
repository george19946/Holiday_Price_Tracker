"""Deterministic, offline implementations of FlightProvider and StayProvider.

Used by every test in this project, by CI (which has no API token and no
network access), and by `holiday-track search --provider fixtures` for
demos. Prices are synthesised from a stable hash of the inputs rather than
randomness, so the same query always returns exactly the same result --
that determinism is what makes snapshot-testing the search engine (phase 3)
possible without a live provider.
"""

from __future__ import annotations

import calendar as _calendar
import hashlib
from datetime import UTC, date, datetime

from holiday_tracker.models import Money, StayQuote
from holiday_tracker.providers.base import FareCalendar

_FIXED_OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# A small rotation of property types so fixture stay results exercise every
# stay filter (exclude_hostels, min_rating, max_centre_km,
# free_cancellation_only) without any of them trivially passing or failing
# every candidate.
_PROPERTY_TYPES = ("hotel", "aparthotel", "hostel", "guesthouse")


def _stable_int(*parts: str, modulo: int) -> int:
    """A deterministic, well-distributed integer in [0, modulo) derived from
    `parts` -- used everywhere below instead of `random` so results are
    reproducible."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:12], 16) % modulo


class FixturesFlightProvider:
    """Synthesises a plausible cheapest-fare-per-day calendar.

    The base fare for a route is derived from a hash of (origin,
    destination) so different routes get different but stable prices; a
    per-day wobble derived from a hash of the date adds the kind of
    day-to-day variation a real fare calendar shows.
    """

    def __init__(self, base_price_range: tuple[int, int] = (40, 220)) -> None:
        self._base_min, self._base_max = base_price_range

    def _base_price(self, origin: str, destination_iata: str) -> int:
        return self._base_min + _stable_int(
            origin, destination_iata, modulo=self._base_max - self._base_min
        )

    def fare_calendar(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> FareCalendar:
        base = self._base_price(origin, destination_iata)
        days_in_month = _calendar.monthrange(year, month)[1]
        prices: dict[date, Money] = {}
        for day in range(1, days_in_month + 1):
            depart = date(year, month, day)
            wobble = _stable_int(origin, destination_iata, depart.isoformat(), modulo=60)
            prices[depart] = Money.from_major(base + wobble, "GBP")

        return FareCalendar(
            origin=origin,
            destination_iata=destination_iata,
            year=year,
            month=month,
            prices=prices,
            observed_at=_FIXED_OBSERVED_AT,
            source="fixtures",
        )


class FixturesStayProvider:
    """Synthesises a shortlist of candidate stays for a city and date range.

    Each of the `count` candidates gets a deterministically-derived nightly
    rate, property type, rating, distance from centre, and cancellation
    policy, so tests can construct a StayFilters that is known in advance to
    accept or reject specific candidates.
    """

    def __init__(self, count: int = 8, base_rate_range: tuple[int, int] = (25, 180)) -> None:
        self._count = count
        self._base_min, self._base_max = base_rate_range

    def search(
        self, city_id: str, check_in: date, check_out: date, adults: int, limit: int = 20
    ) -> list[StayQuote]:
        results: list[StayQuote] = []
        n = min(self._count, limit)
        for i in range(n):
            key = (city_id, check_in.isoformat(), check_out.isoformat(), str(i))
            nightly = self._base_min + _stable_int(*key, modulo=self._base_max - self._base_min)
            property_type = _PROPERTY_TYPES[_stable_int(*key, "type", modulo=len(_PROPERTY_TYPES))]
            rating = 5.5 + _stable_int(*key, "rating", modulo=45) / 10  # 5.5 .. 9.9
            distance_km = round(0.2 + _stable_int(*key, "dist", modulo=80) / 10, 1)  # 0.2 .. 8.1
            free_cancellation = _stable_int(*key, "cancel", modulo=2) == 0

            results.append(
                StayQuote(
                    city_id=city_id,
                    check_in=check_in,
                    check_out=check_out,
                    nightly_rate=Money.from_major(nightly, "GBP"),
                    property_type=property_type,
                    rating=round(rating, 1),
                    distance_km=distance_km,
                    free_cancellation=free_cancellation,
                    observed_at=_FIXED_OBSERVED_AT,
                    source="fixtures",
                    deep_link=None,
                    confidence="observed",
                )
            )
        # Cheapest-first, matching the real StayProvider contract.
        results.sort(key=lambda q: q.nightly_rate.minor_units)
        return results
