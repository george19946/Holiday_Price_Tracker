"""Tests for engine/rank.py: near-miss "what to relax" annotation.

Builds small, hand-constructed sets of Package objects (rather than going
through the full search engine) so each axis -- dates, destination, stay
filter -- can be tested in isolation with an unambiguous expected answer.
"""

from datetime import UTC, date, datetime

from holiday_tracker.engine.rank import describe_relaxation, suggest_relaxation
from holiday_tracker.models import (
    DateRule,
    Money,
    Package,
    SearchSpec,
    StayFilters,
    StayQuote,
)


def _spec(**overrides) -> SearchSpec:
    defaults = dict(
        origins=["LHR"],
        destination="barcelona",
        date_rule=DateRule(window_start=date(2027, 1, 1), window_end=date(2027, 12, 31)),
        budget=Money.from_major(500, "GBP"),
        party_size=2,
    )
    defaults.update(overrides)
    return SearchSpec(**defaults)


def _package(
    destination_city_id="barcelona",
    depart_date=date(2027, 3, 4),
    return_date=date(2027, 3, 7),
    total=500,
    stay=None,
    accommodation_cost=150,
    budget=500,
) -> Package:
    total_cost = Money.from_major(total, "GBP")
    budget_money = Money.from_major(budget, "GBP")
    fits = total_cost.minor_units <= budget_money.minor_units
    return Package(
        destination_city_id=destination_city_id,
        origin="LHR",
        depart_date=depart_date,
        return_date=return_date,
        nights=(return_date - depart_date).days,
        party_size=2,
        flights_cost=Money.from_major(100, "GBP"),
        accommodation_cost=Money.from_major(accommodation_cost, "GBP"),
        spend_cost=total_cost - Money.from_major(100, "GBP") - Money.from_major(accommodation_cost, "GBP"),
        total_cost=total_cost,
        stay=stay,
        fits_budget=fits,
        over_budget_by=None if fits else (total_cost - budget_money),
    )


def _stay(nightly=50, **overrides) -> StayQuote:
    defaults = dict(
        city_id="barcelona",
        check_in=date(2027, 3, 4),
        check_out=date(2027, 3, 7),
        nightly_rate=Money.from_major(nightly, "GBP"),
        property_type="hotel",
        rating=8.0,
        distance_km=1.0,
        free_cancellation=True,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="fixtures",
    )
    defaults.update(overrides)
    return StayQuote(**defaults)


class TestSuggestRelaxationDatesAxis:
    def test_suggests_a_cheaper_date_for_the_same_destination(self):
        anchor = _package(total=540, depart_date=date(2027, 3, 4), return_date=date(2027, 3, 7))
        cheaper_dates = _package(total=480, depart_date=date(2027, 3, 11), return_date=date(2027, 3, 14))
        relaxation = suggest_relaxation(anchor, [anchor, cheaper_dates], _spec(), {})
        assert relaxation is not None
        assert relaxation.axis == "dates"
        assert relaxation.saving == Money.from_major(60, "GBP")
        assert relaxation.closes_gap is True  # 480 <= 500

    def test_does_not_suggest_a_more_expensive_alternative(self):
        anchor = _package(total=540)
        pricier = _package(total=600, depart_date=date(2027, 3, 11), return_date=date(2027, 3, 14))
        relaxation = suggest_relaxation(anchor, [anchor, pricier], _spec(), {})
        assert relaxation is None


class TestSuggestRelaxationDestinationAxis:
    def test_suggests_a_cheaper_destination_on_the_same_dates(self):
        anchor = _package(destination_city_id="barcelona", total=540)
        cheaper_destination = _package(destination_city_id="valencia", total=470)
        relaxation = suggest_relaxation(anchor, [anchor, cheaper_destination], _spec(), {})
        assert relaxation is not None
        assert relaxation.axis == "destination"
        assert "valencia".replace("_", " ").title() in relaxation.description.title()
        assert relaxation.saving == Money.from_major(70, "GBP")


class TestSuggestRelaxationPicksBiggestSaving:
    def test_prefers_whichever_axis_saves_more(self):
        anchor = _package(total=540)
        small_date_saving = _package(total=530, depart_date=date(2027, 3, 11), return_date=date(2027, 3, 14))
        big_destination_saving = _package(destination_city_id="valencia", total=400)
        relaxation = suggest_relaxation(
            anchor, [anchor, small_date_saving, big_destination_saving], _spec(), {}
        )
        assert relaxation is not None
        assert relaxation.axis == "destination"
        assert relaxation.saving == Money.from_major(140, "GBP")


class TestSuggestRelaxationStayAxis:
    def test_relaxing_exclude_hostels_finds_a_cheaper_stay(self):
        spec = _spec(stay_filters=StayFilters(exclude_hostels=True))
        anchor = _package(total=540, stay=_stay(nightly=50, property_type="hotel"), accommodation_cost=150)
        key = (anchor.destination_city_id, anchor.depart_date, anchor.return_date)
        raw_stays = {
            key: [
                _stay(nightly=20, property_type="hostel"),
                _stay(nightly=50, property_type="hotel"),
            ]
        }
        relaxation = suggest_relaxation(anchor, [anchor], spec, raw_stays)
        assert relaxation is not None
        assert relaxation.axis == "stay:exclude_hostels"
        # 3 nights * 1 room * (50 - 20) = 90 saved on accommodation
        assert relaxation.saving == Money.from_major(90, "GBP")

    def test_no_stay_relaxation_when_anchor_has_no_stay(self):
        anchor = _package(total=540, stay=None)
        relaxation = suggest_relaxation(anchor, [anchor], _spec(), {"anything": []})
        assert relaxation is None

    def test_no_stay_relaxation_when_no_filters_are_active(self):
        anchor = _package(total=540, stay=_stay())
        key = (anchor.destination_city_id, anchor.depart_date, anchor.return_date)
        raw_stays = {key: [_stay(nightly=10)]}
        # No active StayFilters means nothing to loosen.
        relaxation = suggest_relaxation(anchor, [anchor], _spec(stay_filters=StayFilters()), raw_stays)
        assert relaxation is None


class TestSuggestRelaxationNoData:
    def test_returns_none_when_nothing_is_cheaper(self):
        anchor = _package(total=540)
        relaxation = suggest_relaxation(anchor, [anchor], _spec(), {})
        assert relaxation is None


class TestDescribeRelaxation:
    def test_message_includes_overage_saving_and_gap_status(self):
        anchor = _package(total=540, budget=500)
        cheaper = _package(total=480, depart_date=date(2027, 3, 11), return_date=date(2027, 3, 14))
        relaxation = suggest_relaxation(anchor, [anchor, cheaper], _spec(), {})
        message = describe_relaxation(anchor, relaxation)
        assert "£540.00" in message
        assert "£40.00 over budget" in message
        assert "saves £60.00" in message
        assert "closes the gap" in message.lower()

    def test_message_when_relaxation_does_not_close_the_gap(self):
        anchor = _package(total=900, budget=500)
        smaller_overage = _package(total=700, depart_date=date(2027, 3, 11), return_date=date(2027, 3, 14))
        relaxation = suggest_relaxation(anchor, [anchor, smaller_overage], _spec(), {})
        message = describe_relaxation(anchor, relaxation)
        assert "still over budget" in message.lower()
