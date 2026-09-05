"""Unit tests for holiday_tracker.dates: DateRule -> concrete date pairs."""

from datetime import date

from holiday_tracker.dates import departure_months, expand_date_pairs
from holiday_tracker.models import DateRule, Weekday


def test_thu_to_sun_across_a_year_matches_iso_weekday_arithmetic():
    """Every Thursday in 2027 should produce exactly one Thu->Sun (3-night) pair,
    and there are 52 Thursdays in 2027 (2027-01-01 is a Friday)."""
    rule = DateRule(
        window_start=date(2027, 1, 1),
        window_end=date(2027, 12, 31),
        depart_dow={Weekday.thu},
        return_dow={Weekday.sun},
        nights_min=3,
        nights_max=3,
    )
    pairs = expand_date_pairs(rule)

    assert len(pairs) == 52
    for depart, return_ in pairs:
        assert depart.isoweekday() == 4  # Thursday
        assert return_.isoweekday() == 7  # Sunday
        assert (return_ - depart).days == 3
        assert rule.window_start <= depart <= rule.window_end


def test_nights_range_produces_multiple_durations_per_departure():
    rule = DateRule(
        window_start=date(2027, 3, 4),  # a Thursday
        window_end=date(2027, 3, 4),
        depart_dow={Weekday.thu},
        nights_min=3,
        nights_max=4,
    )
    pairs = expand_date_pairs(rule)
    assert pairs == [
        (date(2027, 3, 4), date(2027, 3, 7)),
        (date(2027, 3, 4), date(2027, 3, 8)),
    ]


def test_no_dow_restriction_allows_every_day_in_window():
    rule = DateRule(window_start=date(2027, 6, 1), window_end=date(2027, 6, 3), nights_min=1)
    pairs = expand_date_pairs(rule)
    assert pairs == [
        (date(2027, 6, 1), date(2027, 6, 2)),
        (date(2027, 6, 2), date(2027, 6, 3)),
        (date(2027, 6, 3), date(2027, 6, 4)),
    ]


def test_blackout_excludes_overlapping_trips_not_just_exact_dates():
    # A Thu departure whose Sun return falls inside a blackout range must be
    # excluded even though the departure date itself is outside the range.
    rule = DateRule(
        window_start=date(2027, 12, 1),
        window_end=date(2027, 12, 31),
        depart_dow={Weekday.thu},
        return_dow={Weekday.sun},
        nights_min=3,
        nights_max=3,
        blackouts=[(date(2027, 12, 20), date(2028, 1, 3))],
    )
    pairs = expand_date_pairs(rule)
    # Thursdays in Dec 2027: 2, 9, 16, 23, 30. Their Sunday returns: 5, 12,
    # 19, 26, Jan 2. The pair departing 23rd (returning 26th) and 30th
    # (returning Jan 2) both overlap the blackout and must be excluded.
    departs = {d for d, _ in pairs}
    assert departs == {date(2027, 12, 2), date(2027, 12, 9), date(2027, 12, 16)}


def test_single_date_blackout_excludes_trips_spanning_it():
    rule = DateRule(
        window_start=date(2027, 6, 10),
        window_end=date(2027, 6, 10),
        nights_min=6,
        nights_max=6,
        blackouts=[date(2027, 6, 15)],  # falls inside the 10th..16th trip
    )
    assert expand_date_pairs(rule) == []


def test_months_restriction_limits_departure_month():
    rule = DateRule(
        window_start=date(2027, 1, 1),
        window_end=date(2027, 3, 31),
        depart_dow={Weekday.thu},
        nights_min=2,
        nights_max=2,
        months={2},
    )
    pairs = expand_date_pairs(rule)
    assert pairs
    assert all(depart.month == 2 for depart, _ in pairs)


def test_result_is_deduplicated_and_sorted():
    rule = DateRule(window_start=date(2027, 1, 1), window_end=date(2027, 1, 5), nights_min=1)
    pairs = expand_date_pairs(rule)
    assert pairs == sorted(pairs)
    assert len(pairs) == len(set(pairs))


def test_empty_result_when_nothing_satisfies_the_rule():
    # A single-day window can't produce a Thu departure if that day isn't a Thursday.
    rule = DateRule(
        window_start=date(2027, 6, 2),  # a Wednesday
        window_end=date(2027, 6, 2),
        depart_dow={Weekday.thu},
        nights_min=1,
    )
    assert expand_date_pairs(rule) == []


def test_departure_months_covers_whole_window():
    rule = DateRule(window_start=date(2027, 11, 20), window_end=date(2028, 1, 10))
    assert departure_months(rule) == [
        (2027, 11),
        (2027, 12),
        (2028, 1),
    ]


def test_departure_months_respects_month_restriction():
    rule = DateRule(
        window_start=date(2027, 1, 1),
        window_end=date(2027, 12, 31),
        months={3, 7},
    )
    assert departure_months(rule) == [(2027, 3), (2027, 7)]
