"""Tests for the deterministic offline fixtures providers.

These matter beyond "do they run": the whole test suite for the search
engine (phase 3) depends on fixtures being genuinely deterministic and on
producing enough variety (in price, property type, rating, distance,
cancellation policy) to exercise every stay filter -- and, for flights,
on mirroring the *shape* a real free-tier provider was confirmed (against
a live token) to actually return: a sparse set of already-priced
round-trip fares, not a per-day price grid (see providers/base.py and
providers/travelpayouts.py).
"""

from __future__ import annotations

from datetime import date

from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider


class TestFixturesFlightProviderCalendar:
    def test_same_query_is_fully_deterministic(self):
        provider = FixturesFlightProvider()
        first = provider.fare_calendar("LHR", "BCN")
        second = provider.fare_calendar("LHR", "BCN")
        assert first == second

    def test_returns_the_configured_number_of_fares(self):
        provider = FixturesFlightProvider(fare_count=20)
        fares = provider.fare_calendar("LHR", "BCN")
        assert len(fares) == 20

    def test_each_fare_is_a_concrete_already_priced_round_trip(self):
        provider = FixturesFlightProvider()
        for fare in provider.fare_calendar("LHR", "BCN"):
            assert fare.return_date > fare.depart_date
            nights = (fare.return_date - fare.depart_date).days
            assert 2 <= nights <= 10
            assert fare.price.currency == "GBP"
            assert fare.source == "fixtures"

    def test_different_routes_get_different_fares(self):
        provider = FixturesFlightProvider()
        bcn = provider.fare_calendar("LHR", "BCN")
        osl = provider.fare_calendar("LHR", "OSL")
        assert bcn != osl

    def test_fares_are_spread_across_a_wide_window_not_one_month(self):
        # Mirrors what live testing found: real cached fares span a rolling
        # ~10-11 month window, not a single requested month.
        provider = FixturesFlightProvider()
        fares = provider.fare_calendar("LHR", "BCN")
        months = {(f.depart_date.year, f.depart_date.month) for f in fares}
        assert len(months) > 3


class TestFixturesFlightProviderMonthly:
    def test_same_query_is_fully_deterministic(self):
        provider = FixturesFlightProvider()
        first = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        second = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        assert first == second

    def test_fare_falls_within_the_requested_month(self):
        provider = FixturesFlightProvider()
        fare = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        assert fare is not None
        assert fare.depart_date.year == 2027
        assert fare.depart_date.month == 3

    def test_different_months_can_produce_different_fares(self):
        provider = FixturesFlightProvider()
        march = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        april = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 4)
        assert (march.depart_date, march.price) != (april.depart_date, april.price)

    def test_nights_are_within_documented_range(self):
        provider = FixturesFlightProvider()
        fare = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 6)
        nights = (fare.return_date - fare.depart_date).days
        assert 2 <= nights <= 10


class TestFixturesStayProvider:
    def test_same_query_is_fully_deterministic(self):
        provider = FixturesStayProvider()
        args = ("barcelona", date(2027, 3, 4), date(2027, 3, 7), 2)
        first = provider.search(*args)
        second = provider.search(*args)
        assert first == second

    def test_results_are_sorted_cheapest_first(self):
        provider = FixturesStayProvider()
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), 2)
        rates = [q.nightly_rate.minor_units for q in results]
        assert rates == sorted(rates)

    def test_respects_limit(self):
        provider = FixturesStayProvider(count=8)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), 2, limit=3)
        assert len(results) == 3

    def test_produces_a_mix_of_property_types(self):
        provider = FixturesStayProvider(count=20)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), 2, limit=20)
        property_types = {q.property_type for q in results}
        assert "hostel" in property_types
        assert property_types - {"hostel"}  # at least one non-hostel type too

    def test_different_cities_get_different_results(self):
        provider = FixturesStayProvider()
        args = (date(2027, 3, 4), date(2027, 3, 7), 2)
        barcelona = provider.search("barcelona", *args)
        paris = provider.search("paris", *args)
        assert barcelona != paris

    def test_ratings_and_distance_are_within_documented_ranges(self):
        provider = FixturesStayProvider(count=20)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), 2, limit=20)
        for quote in results:
            assert 5.5 <= quote.rating <= 10.0
            assert 0.0 < quote.distance_km <= 8.2
