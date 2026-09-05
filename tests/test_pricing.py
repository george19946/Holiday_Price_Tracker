"""Unit tests for engine/pricing.py: FX conversion and the all-in cost model."""

from datetime import date

import pytest

from holiday_tracker.catalog.loader import DailySpend
from holiday_tracker.engine.pricing import (
    build_package,
    convert,
    price_accommodation,
    price_flights,
    price_spend,
    spend_days_for,
)
from holiday_tracker.models import (
    DateRule,
    Money,
    SearchSpec,
    SpendStyle,
    StayQuote,
)


class TestConvert:
    def test_same_currency_is_a_no_op(self):
        m = Money.from_major(10, "GBP")
        assert convert(m, "GBP") is m

    def test_cross_currency_uses_fx_table(self):
        eur = Money.from_major(100, "EUR")
        gbp = convert(eur, "GBP")
        assert gbp.currency == "GBP"
        assert gbp.amount == pytest.approx(86.0, abs=0.01)

    def test_round_trip_is_approximately_stable(self):
        original = Money.from_major(100, "GBP")
        round_tripped = convert(convert(original, "EUR"), "GBP")
        assert round_tripped.amount == pytest.approx(original.amount, abs=0.01)

    def test_unknown_currency_raises(self):
        with pytest.raises(ValueError, match="no FX rate"):
            convert(Money.from_major(10, "XXX"), "GBP")
        with pytest.raises(ValueError, match="no FX rate"):
            convert(Money.from_major(10, "GBP"), "XXX")


def test_spend_days_equals_nights():
    assert spend_days_for(3) == 3
    assert spend_days_for(1) == 1


def test_price_flights_multiplies_by_party_size():
    fare = Money.from_major(100, "GBP")
    assert price_flights(fare, 3) == Money.from_major(300, "GBP")


def test_price_accommodation_multiplies_nights_and_rooms():
    nightly = Money.from_major(50, "GBP")
    assert price_accommodation(nightly, nights=3, rooms_needed=2) == Money.from_major(300, "GBP")


def test_price_spend_applies_style_multiplier_and_converts_currency():
    daily = DailySpend(city_id="barcelona", food=20, local_transport=5, activities=5, currency="EUR")
    # daily_total = 30 EUR/person/day; normal style; party 2; nights 3 => 180 EUR
    result = price_spend(daily, SpendStyle.normal, party_size=2, nights=3, currency="EUR")
    assert result == Money.from_major(180, "EUR")

    thrifty = price_spend(daily, SpendStyle.thrifty, party_size=2, nights=3, currency="EUR")
    comfortable = price_spend(daily, SpendStyle.comfortable, party_size=2, nights=3, currency="EUR")
    assert thrifty.amount < result.amount < comfortable.amount

    converted = price_spend(daily, SpendStyle.normal, party_size=2, nights=3, currency="GBP")
    assert converted.currency == "GBP"


class TestBuildPackage:
    def _spec(self, **overrides) -> SearchSpec:
        defaults = dict(
            origins=["LHR"],
            destination="barcelona",
            date_rule=DateRule(window_start=date(2027, 1, 1), window_end=date(2027, 12, 31)),
            budget=Money.from_major(500, "GBP"),
            party_size=2,
        )
        defaults.update(overrides)
        return SearchSpec(**defaults)

    def _stay(self, nightly=50) -> StayQuote:
        return StayQuote(
            city_id="barcelona",
            check_in=date(2027, 3, 4),
            check_out=date(2027, 3, 7),
            nightly_rate=Money.from_major(nightly, "GBP"),
            property_type="hotel",
            rating=8.0,
            distance_km=1.0,
            free_cancellation=True,
            observed_at="2026-01-01T00:00:00Z",
            source="fixtures",
        )

    def _daily_spend(self) -> DailySpend:
        return DailySpend(city_id="barcelona", food=20, local_transport=5, activities=5, currency="EUR")

    def test_package_fits_when_total_within_budget(self):
        spec = self._spec(budget=Money.from_major(1000, "GBP"))
        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(80, "GBP"),
            stay=self._stay(nightly=50),
            daily_spend=self._daily_spend(),
        )
        assert package.nights == 3
        assert package.flights_cost == Money.from_major(160, "GBP")  # 80 * 2 people
        assert package.accommodation_cost == Money.from_major(150, "GBP")  # 50 * 3 nights * 1 room
        assert package.fits_budget is True
        assert package.over_budget_by is None

    def test_package_over_budget_reports_the_overage(self):
        spec = self._spec(budget=Money.from_major(50, "GBP"))
        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(80, "GBP"),
            stay=self._stay(nightly=50),
            daily_spend=self._daily_spend(),
        )
        assert package.fits_budget is False
        assert package.over_budget_by is not None
        assert package.over_budget_by == package.total_cost - spec.budget

    def test_package_with_no_stay_has_zero_accommodation_cost(self):
        spec = self._spec(budget=Money.from_major(1000, "GBP"))
        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(80, "GBP"),
            stay=None,
            daily_spend=self._daily_spend(),
        )
        assert package.accommodation_cost == Money.zero("GBP")
        assert package.stay is None

    def test_rooms_needed_affects_accommodation_cost(self):
        spec = self._spec(party_size=5, occupancy_per_room=2, budget=Money.from_major(2000, "GBP"))
        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(80, "GBP"),
            stay=self._stay(nightly=50),
            daily_spend=self._daily_spend(),
        )
        # 5 people / 2 per room => 3 rooms
        assert package.accommodation_cost == Money.from_major(50 * 3 * 3, "GBP")

    def test_breakdown_property_sums_to_total(self):
        spec = self._spec(budget=Money.from_major(1000, "GBP"))
        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(80, "GBP"),
            stay=self._stay(nightly=50),
            daily_spend=self._daily_spend(),
        )
        breakdown = package.breakdown
        assert breakdown["flights"] + breakdown["accommodation"] + breakdown["spend"] == breakdown["total"]
