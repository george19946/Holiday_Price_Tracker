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
from datetime import UTC, date, datetime, timedelta

from holiday_tracker.models import Money, StayQuote
from holiday_tracker.providers.base import CalendarFare

_FIXED_OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# A fixed anchor, not "today" -- fixtures must return the same thing forever
# regardless of when the test suite runs. Mirrors fare_calendar's real-world
# shape (see providers/base.py): a rolling window of already-priced
# round-trip fares, not a clean per-day grid.
_CALENDAR_ANCHOR = date(2027, 1, 1)
_CALENDAR_WINDOW_DAYS = 365

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
    """Synthesises the same *shape* of data the real Travelpayouts adapter
    was confirmed (against a live token) to actually return: a sparse set
    of already-priced round-trip fares scattered across a rolling window
    for fare_calendar(), and a single cheapest fare for a requested month
    from cheapest_fare_in_month() -- not a clean per-day price grid. See
    providers/base.py and providers/travelpayouts.py for why.

    Everything is derived from a stable hash of the inputs, never
    `random`, so the same query always returns exactly the same result.
    """

    def __init__(
        self, base_price_range: tuple[int, int] = (40, 220), fare_count: int = 45
    ) -> None:
        self._base_min, self._base_max = base_price_range
        self._fare_count = fare_count

    def _base_price(self, origin: str, destination_iata: str) -> int:
        return self._base_min + _stable_int(
            origin, destination_iata, modulo=self._base_max - self._base_min
        )

    def fare_calendar(self, origin: str, destination_iata: str) -> list[CalendarFare]:
        base = self._base_price(origin, destination_iata)
        fares: list[CalendarFare] = []
        for i in range(self._fare_count):
            key = (origin, destination_iata, str(i))
            offset_days = _stable_int(*key, "offset", modulo=_CALENDAR_WINDOW_DAYS)
            nights = 2 + _stable_int(*key, "nights", modulo=9)  # 2..10
            depart = _CALENDAR_ANCHOR + timedelta(days=offset_days)
            return_date = depart + timedelta(days=nights)
            wobble = _stable_int(*key, "price", modulo=80)
            fares.append(
                CalendarFare(
                    depart_date=depart,
                    return_date=return_date,
                    price=Money.from_major(base + wobble, "GBP"),
                    observed_at=_FIXED_OBSERVED_AT,
                    source="fixtures",
                )
            )
        return fares

    def cheapest_fare_in_month(
        self, origin: str, destination_iata: str, year: int, month: int
    ) -> CalendarFare | None:
        base = self._base_price(origin, destination_iata)
        key = (origin, destination_iata, str(year), str(month))
        days_in_month = _calendar.monthrange(year, month)[1]
        day_of_month = 1 + _stable_int(*key, "day", modulo=days_in_month)
        nights = 2 + _stable_int(*key, "nights", modulo=9)
        depart = date(year, month, day_of_month)
        wobble = _stable_int(*key, "price", modulo=60)
        return CalendarFare(
            depart_date=depart,
            return_date=depart + timedelta(days=nights),
            price=Money.from_major(base + wobble, "GBP"),
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
