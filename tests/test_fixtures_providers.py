"""Tests for the deterministic offline fixtures providers.

These matter beyond "do they run": the whole test suite for the search
engine (phase 3) depends on fixtures being genuinely deterministic and on
producing enough variety (in price, property type, rating, distance,
cancellation policy) to exercise every stay filter.
"""

from __future__ import annotations

from datetime import date

from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider


class TestFixturesFlightProvider:
    def test_same_query_is_fully_deterministic(self):
        provider = FixturesFlightProvider()
        first = provider.fare_calendar("LHR", "BCN", 2027, 3)
        second = provider.fare_calendar("LHR", "BCN", 2027, 3)
        assert first.prices == second.prices

    def test_calendar_covers_every_day_of_the_month(self):
        provider = FixturesFlightProvider()
        calendar = provider.fare_calendar("LHR", "BCN", 2027, 4)  # April has 30 days
        assert len(calendar.prices) == 30
        assert set(calendar.prices) == {date(2027, 4, d) for d in range(1, 31)}

    def test_different_routes_get_different_base_prices(self):
        provider = FixturesFlightProvider()
        bcn = provider.fare_calendar("LHR", "BCN", 2027, 3)
        osl = provider.fare_calendar("LHR", "OSL", 2027, 3)
        assert bcn.prices != osl.prices

    def test_prices_are_within_the_configured_range(self):
        provider = FixturesFlightProvider(base_price_range=(50, 100))
        calendar = provider.fare_calendar("LHR", "BCN", 2027, 3)
        for price in calendar.prices.values():
            # base in [50, 100) plus a wobble of up to 59
            assert 50 <= price.amount <= 100 + 59

    def test_source_and_currency(self):
        provider = FixturesFlightProvider()
        calendar = provider.fare_calendar("LHR", "BCN", 2027, 3)
        assert calendar.source == "fixtures"
        assert calendar.origin == "LHR"
        assert calendar.destination_iata == "BCN"
        any_price = next(iter(calendar.prices.values()))
        assert any_price.currency == "GBP"


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
