"""Unit tests for engine/filters.py: StayFilters predicates."""

from datetime import UTC, date, datetime

import pytest

from holiday_tracker.engine.filters import (
    binding_filter,
    cheapest_passing,
    is_hostel_like,
    passes_filters,
)
from holiday_tracker.models import Money, StayFilters, StayQuote


def _stay(**overrides) -> StayQuote:
    defaults = dict(
        city_id="barcelona",
        check_in=date(2027, 3, 4),
        check_out=date(2027, 3, 7),
        nightly_rate=Money.from_major(50, "GBP"),
        property_type="hotel",
        rating=8.0,
        distance_km=1.0,
        free_cancellation=True,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="fixtures",
    )
    defaults.update(overrides)
    return StayQuote(**defaults)


class TestIsHostelLike:
    @pytest.mark.parametrize("property_type", ["hostel", "Hostel", "youth hostel", "dorm room", "backpacker inn"])
    def test_matches_hostel_keywords(self, property_type):
        assert is_hostel_like(_stay(property_type=property_type)) is True

    @pytest.mark.parametrize("property_type", ["hotel", "aparthotel", "guesthouse", "resort"])
    def test_does_not_match_non_hostel_types(self, property_type):
        assert is_hostel_like(_stay(property_type=property_type)) is False


class TestBindingFilter:
    def test_no_filters_always_passes(self):
        assert binding_filter(_stay(), StayFilters()) is None

    def test_exclude_hostels_binds_on_hostel(self):
        stay = _stay(property_type="hostel")
        assert binding_filter(stay, StayFilters(exclude_hostels=True)) == "exclude_hostels"

    def test_min_rating_binds_when_below_threshold(self):
        stay = _stay(rating=6.0)
        assert binding_filter(stay, StayFilters(min_rating=7.5)) == "min_rating"

    def test_min_rating_binds_when_rating_missing(self):
        stay = _stay(rating=None)
        assert binding_filter(stay, StayFilters(min_rating=7.5)) == "min_rating"

    def test_max_centre_km_binds_when_too_far(self):
        stay = _stay(distance_km=5.0)
        assert binding_filter(stay, StayFilters(max_centre_km=3.0)) == "max_centre_km"

    def test_max_centre_km_binds_when_distance_missing(self):
        stay = _stay(distance_km=None)
        assert binding_filter(stay, StayFilters(max_centre_km=3.0)) == "max_centre_km"

    def test_free_cancellation_binds_when_not_refundable(self):
        stay = _stay(free_cancellation=False)
        assert (
            binding_filter(stay, StayFilters(free_cancellation_only=True))
            == "free_cancellation_only"
        )

    def test_checks_in_order_and_reports_first_failure(self):
        stay = _stay(property_type="hostel", rating=2.0)
        filters = StayFilters(exclude_hostels=True, min_rating=9.0)
        assert binding_filter(stay, filters) == "exclude_hostels"

    def test_passing_stay_returns_none(self):
        stay = _stay(property_type="hotel", rating=9.0, distance_km=1.0, free_cancellation=True)
        filters = StayFilters(
            exclude_hostels=True, min_rating=7.5, max_centre_km=3.0, free_cancellation_only=True
        )
        assert binding_filter(stay, filters) is None
        assert passes_filters(stay, filters) is True


class TestCheapestPassing:
    def test_returns_first_passing_stay_in_cheapest_first_list(self):
        stays = [
            _stay(property_type="hostel", nightly_rate=Money.from_major(20, "GBP")),
            _stay(property_type="hotel", nightly_rate=Money.from_major(40, "GBP")),
            _stay(property_type="hotel", nightly_rate=Money.from_major(60, "GBP")),
        ]
        result = cheapest_passing(stays, StayFilters(exclude_hostels=True))
        assert result is not None
        assert result.nightly_rate == Money.from_major(40, "GBP")

    def test_returns_none_when_nothing_passes(self):
        stays = [_stay(property_type="hostel")]
        assert cheapest_passing(stays, StayFilters(exclude_hostels=True)) is None

    def test_empty_list_returns_none(self):
        assert cheapest_passing([], StayFilters()) is None
