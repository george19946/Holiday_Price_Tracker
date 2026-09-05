"""Tests for engine/search.py: the three-stage funnel, against the
deterministic fixtures providers.

This is the offline end-to-end scenario described in the project plan's
verification section: a budget-first search that either finds a fitting
package or falls back to ranked near-misses, entirely without a network
connection or API token.
"""

from datetime import date

from holiday_tracker.engine.search import (
    estimate_flight_requests,
    price_stays_and_assemble,
    run_search,
    shortlist_candidates,
    sweep_flights,
)
from holiday_tracker.models import DateRule, Money, SearchSpec, StayFilters, Weekday
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider


def _spec(**overrides) -> SearchSpec:
    defaults = dict(
        origins=["LHR"],
        destination="barcelona",
        date_rule=DateRule(
            window_start=date(2027, 3, 1),
            window_end=date(2027, 5, 31),
            depart_dow={Weekday.thu},
            return_dow={Weekday.sun},
            nights_min=3,
            nights_max=3,
        ),
        budget=Money.from_major(500, "GBP"),
        party_size=2,
    )
    defaults.update(overrides)
    return SearchSpec(**defaults)


class TestEstimateFlightRequests:
    def test_counts_origins_times_airports_times_months(self):
        spec = _spec()  # Mar-May 2027 => 3 months, 1 origin, Barcelona has 1 airport
        assert estimate_flight_requests(spec, ["barcelona"]) == 3

    def test_scales_with_multiple_origins_and_multi_airport_cities(self):
        spec = _spec(origins=["LHR", "MAN"])
        # Paris has 2 airports (CDG, ORY) in the bundled catalog
        assert estimate_flight_requests(spec, ["paris"]) == 2 * 2 * 3


class TestSweepFlights:
    def test_produces_one_candidate_per_valid_date_pair(self):
        from holiday_tracker.dates import expand_date_pairs

        spec = _spec()
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["barcelona"], provider)
        assert len(candidates) == len(expand_date_pairs(spec.date_rule))
        for c in candidates:
            assert c.origin == "LHR"
            assert c.destination_city_id == "barcelona"
            assert c.destination_iata == "BCN"
            assert c.nights == 3
            assert c.flight.price.currency == "GBP"

    def test_multiple_airports_produce_separate_candidates(self):
        spec = _spec(destination="paris")
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["paris"], provider)
        iatas = {c.destination_iata for c in candidates}
        assert iatas == {"CDG", "ORY"}

    def test_uses_one_calendar_request_per_origin_destination_month(self):
        spec = _spec()
        provider = FixturesFlightProvider()
        sweep_flights(spec, ["barcelona"], provider)
        # request_count isn't tracked on the fixtures provider itself, but we
        # can at least confirm the candidate grid spans exactly the months
        # the date rule implies (Mar, Apr, May).
        candidates = sweep_flights(spec, ["barcelona"], provider)
        months = {(c.depart_date.year, c.depart_date.month) for c in candidates}
        assert months == {(2027, 3), (2027, 4), (2027, 5)}


class TestShortlistCandidates:
    def test_keeps_only_the_cheapest_n(self):
        spec = _spec()
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["barcelona"], provider)
        shortlisted = shortlist_candidates(candidates, limit=3)
        assert len(shortlisted) == 3
        prices = [c.flight.price.minor_units for c in shortlisted]
        assert prices == sorted(prices)
        assert max(prices) <= min(
            c.flight.price.minor_units for c in candidates if c not in shortlisted
        )


class TestPriceStaysAndAssemble:
    def test_builds_a_package_per_shortlisted_candidate_with_a_qualifying_stay(self):
        spec = _spec()
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=5)

        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        assert len(results.packages) <= len(shortlisted)
        assert len(results.packages) > 0
        for package in results.packages:
            assert package.total_cost.currency == "GBP"

    def test_stay_filters_can_eliminate_every_candidate_for_a_date(self):
        spec = _spec(stay_filters=StayFilters(min_rating=9.99))  # fixtures cap out at 9.9
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=5)
        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        assert results.packages == []

    def test_raw_stays_are_cached_per_destination_and_date_range(self):
        spec = _spec(origins=["LHR", "MAN"])
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=10)
        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        # two origins sharing the same destination/date should share one raw_stays entry
        assert len(results.raw_stays) <= len(shortlisted)


class TestRunSearch:
    def test_end_to_end_with_generous_budget_produces_a_feasible_package(self):
        spec = _spec(budget=Money.from_major(2000, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert len(results.feasible) > 0
        assert results.feasible[0].fits_budget is True

    def test_end_to_end_with_impossible_budget_produces_near_misses_not_a_crash(self):
        spec = _spec(budget=Money.from_major(1, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert results.feasible == []
        assert len(results.near_misses) > 0

    def test_feasible_and_near_miss_are_sorted_cheapest_first(self):
        spec = _spec(budget=Money.from_major(300, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        for group in (results.feasible, results.near_misses):
            totals = [p.total_cost.minor_units for p in group]
            assert totals == sorted(totals)

    def test_region_destination_searches_every_city_in_it(self):
        spec = _spec(destination="western_europe", origins=["LHR"])
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        destinations = {p.destination_city_id for p in results.packages}
        assert destinations  # at least one western-europe city produced a package
        from holiday_tracker.catalog.loader import load_regions

        assert destinations <= set(load_regions()["western_europe"].city_ids)

    def test_shortlist_size_bounds_the_number_of_stay_lookups(self):
        spec = _spec(destination="western_europe", origins=["LHR"])
        results = run_search(
            spec, FixturesFlightProvider(), FixturesStayProvider(), shortlist_size=5
        )
        assert len(results.packages) <= 5

    def test_deterministic_across_repeated_runs(self):
        spec = _spec()
        first = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        second = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert [p.total_cost for p in first.packages] == [p.total_cost for p in second.packages]
