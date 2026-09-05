"""Pure date-rule expansion and matching: DateRule <-> concrete (depart,
return) pairs.

No I/O happens here. This is the seam that makes the whole search testable
without a network, and it is also directly useful on its own — the CLI
exposes expand_date_pairs() as a debug command so a date rule's meaning can
be checked before spending any API requests on it.

matches_date_rule() is the other direction: given a *specific* date pair a
provider already handed us (a real cached fare, not one we chose), does it
satisfy the rule? This matters because the real Travelpayouts flight
calendar doesn't let us ask "what does this exact date cost" — it hands
back whichever concrete round-trip fares it happens to have cached (see
providers/travelpayouts.py), and we have to check each one against the
rule rather than generate the pairs ourselves.
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


def matches_date_rule(depart: date, return_: date, rule: DateRule) -> bool:
    """Does this specific (depart, return) pair satisfy `rule`?

    Checks the departure window and month restriction against `depart`
    only (a rule describes when you can *leave*, not how long the trip
    runs past the window) — everything else (weekdays, nights range,
    blackouts) is checked against the actual pair.
    """
    if not (rule.window_start <= depart <= rule.window_end):
        return False
    if rule.months is not None and depart.month not in rule.months:
        return False
    if rule.depart_dow and ISO_WEEKDAY_TO_ENUM[depart.isoweekday()] not in rule.depart_dow:
        return False
    if rule.return_dow and ISO_WEEKDAY_TO_ENUM[return_.isoweekday()] not in rule.return_dow:
        return False
    nights = (return_ - depart).days
    if not (rule.nights_min <= nights <= rule.nights_max):
        return False
    return not _overlaps_any_blackout(depart, return_, rule.blackouts)


def expand_date_pairs(rule: DateRule) -> list[tuple[date, date]]:
    """Expand a DateRule into a sorted, deduplicated list of every concrete
    (depart_date, return_date) pair satisfying it -- the "what dates could
    this possibly mean" direction, used for display (`holiday-track dates`)
    and to know which months to probe (departure_months()). Equivalent to
    (but far cheaper than) generating every pair in the window and nights
    range and keeping the ones matches_date_rule() accepts.
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
            if matches_date_rule(day, return_date, rule):
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
