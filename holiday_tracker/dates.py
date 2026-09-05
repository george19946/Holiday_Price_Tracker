"""Pure date-rule expansion: DateRule -> concrete (depart, return) pairs.

No I/O happens here. This is the seam that makes the whole search testable
without a network, and it is also directly useful on its own — the CLI
exposes it as a debug command so a date rule's meaning can be checked before
spending any API requests on it.
"""

from __future__ import annotations

from datetime import date, timedelta

from holiday_tracker.models import ISO_WEEKDAY_TO_ENUM, DateRule

_ONE_DAY = timedelta(days=1)


def _overlaps_any_blackout(
    start: date, end: date, blackouts: list[tuple[date, date]]
) -> bool:
    """True if the closed interval [start, end] overlaps any blackout range.

    A trip is excluded if any day of it -- not just the depart or return date
    -- falls inside a blackout range, since a blackout typically means "don't
    be away over this period" (e.g. Christmas, a work commitment).
    """
    return any(start <= b_end and end >= b_start for b_start, b_end in blackouts)


def expand_date_pairs(rule: DateRule) -> list[tuple[date, date]]:
    """Expand a DateRule into a sorted, deduplicated list of concrete
    (depart_date, return_date) pairs satisfying every constraint:
    within the window, on an allowed departure/return weekday (if
    restricted), within the nights range, in an allowed month (if
    restricted), and not overlapping any blackout range.
    """
    pairs: set[tuple[date, date]] = set()

    day = rule.window_start
    while day <= rule.window_end:
        if rule.months is not None and day.month not in rule.months:
            day += _ONE_DAY
            continue

        if rule.depart_dow and ISO_WEEKDAY_TO_ENUM[day.isoweekday()] not in rule.depart_dow:
            day += _ONE_DAY
            continue

        for nights in range(rule.nights_min, rule.nights_max + 1):
            return_date = day + timedelta(days=nights)

            if rule.return_dow and ISO_WEEKDAY_TO_ENUM[return_date.isoweekday()] not in rule.return_dow:
                continue

            if _overlaps_any_blackout(day, return_date, rule.blackouts):
                continue

            pairs.add((day, return_date))

        day += _ONE_DAY

    return sorted(pairs)


def departure_months(rule: DateRule) -> list[tuple[int, int]]:
    """Distinct (year, month) pairs that could contain a valid departure date.

    Used by the flight sweep (engine/search.py) to know which monthly fare
    calendars to fetch, without re-deriving this from the rule itself.
    """
    months: set[tuple[int, int]] = set()
    day = rule.window_start
    while day <= rule.window_end:
        if rule.months is None or day.month in rule.months:
            months.add((day.year, day.month))
        day += _ONE_DAY
    return sorted(months)
