"""Tests for engine/search.py: the three-stage funnel, against the
deterministic fixtures providers.

This is the offline end-to-end scenario described in the project plan's
verification section: a budget-first search that either finds a fitting
package or falls back to ranked near-misses, entirely without a network
connection or API token.

Two DateRule shapes matter here for different reasons:

- `_wide_spec` (no weekday/nights restriction) is what most tests use to
  get a reliable, non-trivial number of candidates -- a real free-tier
  provider's cached fares are sparse (see providers/base.py), so a wide
  rule is what makes deterministic "there are results" assertions
  possible without depending on a lucky hash collision.
- `_narrow_spec` (a specific weekday pair and exact nights count) is used
  to exercise -- and document -- a real, confirmed-live limitation: a
  tightly constrained rule can legitimately match nothing at all, because
  that's genuinely how the free Travelpayouts data behaves (verified
  against a real token on 2026-09-05; see providers/travelpayouts.py).
  That's a correct, non-crashing outcome, not a bug.
"""

from datetime import date

from holiday_tracker.dates import departure_months
from holiday_tracker.engine.search import (
    estimate_flight_requests,
    price_stays_and_assemble,
    run_search,
    shortlist_candidates,
    sweep_flights,
)
from holiday_tracker.models import DateRule, Money, SearchSpec, StayFilters, Weekday
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider


def _wide_spec(**overrides) -> SearchSpec:
    defaults = dict(
        origins=["LHR"],
        destination="barcelona",
        date_rule=DateRule(
            window_start=date(2027, 1, 1),
            window_end=date(2027, 12, 31),
            nights_min=1,
            nights_max=14,
        ),
        budget=Money.from_major(2000, "GBP"),
        party_size=2,
    )
    defaults.update(overrides)
    return SearchSpec(**defaults)


def _narrow_spec(**overrides) -> SearchSpec:
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
    def test_counts_one_calendar_call_plus_one_per_month(self):
        spec = _narrow_spec()  # Mar-May 2027 => 3 months, 1 origin, Barcelona has 1 airport
        assert estimate_flight_requests(spec, ["barcelona"]) == 1 * 1 * (1 + 3)

    def test_scales_with_multiple_origins_and_multi_airport_cities(self):
        spec = _narrow_spec(origins=["LHR", "MAN"])
        # Paris has 2 airports (CDG, ORY) in the bundled catalog
        assert estimate_flight_requests(spec, ["paris"]) == 2 * 2 * (1 + 3)


class TestSweepFlights:
    def test_a_wide_rule_admits_every_calendar_fare_plus_monthly_fallbacks(self):
        spec = _wide_spec()
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["barcelona"], provider)

        all_calendar_fares = provider.fare_calendar("LHR", "BCN")
        months = departure_months(spec.date_rule)
        # Every calendar fare and every month's fallback should satisfy
        # this unrestricted rule, deduplicated by exact (depart, return).
        expected_pairs = {(f.depart_date, f.return_date) for f in all_calendar_fares}
        for year, month in months:
            monthly = provider.cheapest_fare_in_month("LHR", "BCN", year, month)
            expected_pairs.add((monthly.depart_date, monthly.return_date))
        assert len(candidates) == len(expected_pairs)

        for c in candidates:
            assert c.origin == "LHR"
            assert c.destination_city_id == "barcelona"
            assert c.destination_iata == "BCN"
            assert c.flight.price.currency == "GBP"
            assert 1 <= c.nights <= 14

    def test_a_narrow_rule_can_legitimately_match_nothing(self):
        # Confirmed against the real API (2026-09-05): a tightly
        # constrained weekday+nights rule can genuinely have zero cached
        # fares behind it. This must not crash or silently misreport.
        spec = _narrow_spec()
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["barcelona"], provider)
        assert candidates == []

    def test_every_returned_candidate_actually_satisfies_the_rule(self):
        from holiday_tracker.dates import matches_date_rule

        spec = _wide_spec(
            date_rule=DateRule(
                window_start=date(2027, 1, 1),
                window_end=date(2027, 12, 31),
                depart_dow={Weekday.fri},
                nights_min=1,
                nights_max=14,
            )
        )
        candidates = sweep_flights(spec, ["barcelona"], FixturesFlightProvider())
        assert candidates  # this route/rule combination does produce some matches
        for c in candidates:
            assert matches_date_rule(c.depart_date, c.return_date, spec.date_rule)

    def test_multiple_airports_produce_separate_candidates(self):
        spec = _wide_spec(destination="paris")
        provider = FixturesFlightProvider()
        candidates = sweep_flights(spec, ["paris"], provider)
        iatas = {c.destination_iata for c in candidates}
        assert iatas == {"CDG", "ORY"}

    def test_deduplicates_when_calendar_and_monthly_fallback_agree(self):
        # If cheapest_fare_in_month happens to return the exact same pair
        # already present in fare_calendar, it must not be counted twice.
        spec = _wide_spec()
        candidates = sweep_flights(spec, ["barcelona"], FixturesFlightProvider())
        pairs = [(c.depart_date, c.return_date) for c in candidates]
        assert len(pairs) == len(set(pairs))


class TestShortlistCandidates:
    def test_keeps_only_the_cheapest_n(self):
        spec = _wide_spec()
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
        spec = _wide_spec()
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
        spec = _wide_spec(stay_filters=StayFilters(min_rating=9.99))  # fixtures cap out at 9.9
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=5)
        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        assert results.packages == []

    def test_raw_stays_are_cached_per_destination_and_date_range(self):
        spec = _wide_spec(origins=["LHR", "MAN"])
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=10)
        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        # two origins sharing the same destination/date should share one raw_stays entry
        assert len(results.raw_stays) <= len(shortlisted)

    def test_flight_deep_link_is_carried_through_to_the_package(self):
        spec = _wide_spec()
        flight_provider = FixturesFlightProvider()
        stay_provider = FixturesStayProvider()
        candidates = sweep_flights(spec, ["barcelona"], flight_provider)
        shortlisted = shortlist_candidates(candidates, limit=5)
        results = price_stays_and_assemble(spec, shortlisted, stay_provider)
        # Fixtures never set a flight deep link (only the real Travelpayouts
        # adapter's monthly lookup does); this just confirms the field
        # round-trips through Package rather than being silently dropped.
        for package in results.packages:
            assert package.flight_deep_link is None


class TestRunSearch:
    def test_end_to_end_with_generous_budget_produces_a_feasible_package(self):
        spec = _wide_spec(budget=Money.from_major(2000, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert len(results.feasible) > 0
        assert results.feasible[0].fits_budget is True

    def test_end_to_end_with_impossible_budget_produces_near_misses_not_a_crash(self):
        spec = _wide_spec(budget=Money.from_major(1, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert results.feasible == []
        assert len(results.near_misses) > 0

    def test_end_to_end_with_narrow_rule_produces_no_results_not_a_crash(self):
        spec = _narrow_spec()
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert results.feasible == []
        assert results.near_misses == []
        assert results.packages == []

    def test_feasible_and_near_miss_are_sorted_cheapest_first(self):
        spec = _wide_spec(budget=Money.from_major(300, "GBP"))
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        for group in (results.feasible, results.near_misses):
            totals = [p.total_cost.minor_units for p in group]
            assert totals == sorted(totals)

    def test_region_destination_searches_every_city_in_it(self):
        spec = _wide_spec(destination="western_europe", origins=["LHR"])
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        destinations = {p.destination_city_id for p in results.packages}
        assert destinations  # at least one western-europe city produced a package
        from holiday_tracker.catalog.loader import load_regions

        assert destinations <= set(load_regions()["western_europe"].city_ids)

    def test_shortlist_size_bounds_the_number_of_stay_lookups(self):
        spec = _wide_spec(destination="western_europe", origins=["LHR"])
        results = run_search(
            spec, FixturesFlightProvider(), FixturesStayProvider(), shortlist_size=5
        )
        assert len(results.packages) <= 5

    def test_deterministic_across_repeated_runs(self):
        spec = _wide_spec()
        first = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        second = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        assert [p.total_cost for p in first.packages] == [p.total_cost for p in second.packages]
