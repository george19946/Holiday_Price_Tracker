"""Unit tests for holiday_tracker.models: Money, DateRule, StayFilters, SearchSpec."""

from datetime import date

import pytest
from pydantic import ValidationError

from holiday_tracker.models import (
    DateRule,
    Money,
    SearchSpec,
    SpendStyle,
    StayFilters,
    Weekday,
)


class TestMoney:
    def test_from_major_round_trips(self):
        m = Money.from_major(12.5, "GBP")
        assert m.minor_units == 1250
        assert m.amount == 12.5

    def test_addition_same_currency(self):
        assert Money.from_major(10) + Money.from_major(5) == Money.from_major(15)

    def test_addition_currency_mismatch_raises(self):
        with pytest.raises(ValueError, match="currency mismatch"):
            Money.from_major(10, "GBP") + Money.from_major(10, "EUR")

    def test_subtraction(self):
        assert Money.from_major(10) - Money.from_major(3) == Money.from_major(7)

    def test_multiplication_by_scalar(self):
        assert Money.from_major(10) * 3 == Money.from_major(30)
        assert 3 * Money.from_major(10) == Money.from_major(30)

    def test_ordering(self):
        assert Money.from_major(5) < Money.from_major(10)
        assert Money.from_major(10) > Money.from_major(5)
        assert Money.from_major(10) >= Money.from_major(10)
        assert sorted([Money.from_major(3), Money.from_major(1), Money.from_major(2)]) == [
            Money.from_major(1),
            Money.from_major(2),
            Money.from_major(3),
        ]

    def test_no_float_drift_over_many_additions(self):
        total = Money.zero("GBP")
        for _ in range(1000):
            total = total + Money.from_major(0.1)
        assert total == Money.from_major(100.0)

    def test_str_formatting(self):
        assert str(Money.from_major(1234.5, "GBP")) == "£1,234.50"
        assert str(Money.from_major(10, "EUR")) == "€10.00"
        assert str(Money.from_major(10, "PLN")) == "PLN 10.00"

    def test_frozen(self):
        m = Money.from_major(10)
        with pytest.raises(ValidationError):
            m.minor_units = 999


class TestDateRule:
    def test_window_start_after_end_rejected(self):
        with pytest.raises(ValidationError, match="window_start"):
            DateRule(window_start=date(2027, 6, 1), window_end=date(2027, 1, 1))

    def test_nights_max_below_min_rejected(self):
        with pytest.raises(ValidationError, match="nights_max"):
            DateRule(
                window_start=date(2027, 1, 1),
                window_end=date(2027, 12, 31),
                nights_min=5,
                nights_max=2,
            )

    def test_nights_min_below_one_rejected(self):
        with pytest.raises(ValidationError, match="nights_min"):
            DateRule(
                window_start=date(2027, 1, 1),
                window_end=date(2027, 12, 31),
                nights_min=0,
            )

    def test_invalid_month_rejected(self):
        with pytest.raises(ValidationError, match="months"):
            DateRule(
                window_start=date(2027, 1, 1),
                window_end=date(2027, 12, 31),
                months={13},
            )

    def test_bare_date_blackout_normalized_to_range(self):
        rule = DateRule(
            window_start=date(2027, 1, 1),
            window_end=date(2027, 12, 31),
            blackouts=[date(2027, 6, 15)],
        )
        assert rule.blackouts == [(date(2027, 6, 15), date(2027, 6, 15))]

    def test_blackout_range_start_after_end_rejected(self):
        with pytest.raises(ValidationError, match="blackout"):
            DateRule(
                window_start=date(2027, 1, 1),
                window_end=date(2027, 12, 31),
                blackouts=[(date(2027, 6, 20), date(2027, 6, 10))],
            )

    def test_depart_dow_accepts_enum_members(self):
        rule = DateRule(
            window_start=date(2027, 1, 1),
            window_end=date(2027, 12, 31),
            depart_dow={Weekday.thu},
        )
        assert rule.depart_dow == {Weekday.thu}


class TestStayFilters:
    def test_defaults_are_permissive(self):
        filters = StayFilters()
        assert filters.exclude_hostels is False
        assert filters.min_rating is None
        assert filters.max_centre_km is None
        assert filters.free_cancellation_only is False

    def test_min_rating_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="min_rating"):
            StayFilters(min_rating=11)
        with pytest.raises(ValidationError, match="min_rating"):
            StayFilters(min_rating=-1)

    def test_max_centre_km_must_be_positive(self):
        with pytest.raises(ValidationError, match="max_centre_km"):
            StayFilters(max_centre_km=0)
        with pytest.raises(ValidationError, match="max_centre_km"):
            StayFilters(max_centre_km=-3)


class TestSearchSpec:
    def _rule(self):
        return DateRule(window_start=date(2027, 1, 1), window_end=date(2027, 12, 31))

    def test_origins_normalized_to_uppercase(self):
        spec = SearchSpec(
            origins=["lhr", " lgw "],
            destination="barcelona",
            date_rule=self._rule(),
            budget=Money.from_major(500),
        )
        assert spec.origins == ["LHR", "LGW"]

    def test_empty_origins_rejected(self):
        with pytest.raises(ValidationError, match="origin"):
            SearchSpec(
                origins=[],
                destination="barcelona",
                date_rule=self._rule(),
                budget=Money.from_major(500),
            )

    def test_empty_destination_rejected(self):
        with pytest.raises(ValidationError, match="destination"):
            SearchSpec(
                origins=["LHR"],
                destination="   ",
                date_rule=self._rule(),
                budget=Money.from_major(500),
            )

    def test_defaults(self):
        spec = SearchSpec(
            origins=["LHR"],
            destination="barcelona",
            date_rule=self._rule(),
            budget=Money.from_major(500),
        )
        assert spec.party_size == 1
        assert spec.occupancy_per_room == 2
        assert spec.spend_style == SpendStyle.normal
        assert spec.stay_filters == StayFilters()

    @pytest.mark.parametrize(
        ("party_size", "occupancy", "expected_rooms"),
        [(1, 2, 1), (2, 2, 1), (3, 2, 2), (4, 2, 2), (5, 2, 3), (5, 3, 2)],
    )
    def test_rooms_needed_rounds_up(self, party_size, occupancy, expected_rooms):
        spec = SearchSpec(
            origins=["LHR"],
            destination="barcelona",
            date_rule=self._rule(),
            budget=Money.from_major(500),
            party_size=party_size,
            occupancy_per_room=occupancy,
        )
        assert spec.rooms_needed == expected_rooms

    def test_party_size_must_be_positive(self):
        with pytest.raises(ValidationError):
            SearchSpec(
                origins=["LHR"],
                destination="barcelona",
                date_rule=self._rule(),
                budget=Money.from_major(500),
                party_size=0,
            )
