"""Tests for the Hotellook stay-price adapter, against a mocked transport
shaped like the documented API responses -- no real network call.

See providers/hotellook.py's module docstring for the caveats about which
parts of this shape are not yet confirmed against a live token.
"""

from __future__ import annotations

from datetime import date

import httpx

from holiday_tracker.providers.hotellook import (
    CACHE_URL,
    STATIC_HOTELS_URL,
    HotellookStayProvider,
    _haversine_km,
)
from holiday_tracker.providers.http import CachedHttpClient, ResponseCache


def _provider(handler, currency: str = "gbp") -> HotellookStayProvider:
    transport = httpx.MockTransport(handler)
    http_client = CachedHttpClient(
        cache=ResponseCache(":memory:"),
        client=httpx.Client(transport=transport),
        sleep=lambda _seconds: None,  # skip real retry backoff in tests
    )
    return HotellookStayProvider(http_client, token="test-token", currency=currency)


def test_search_parses_documented_shape_and_computes_nightly_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/cache.json":
            assert request.url.params["location"] == "barcelona"
            return httpx.Response(
                200,
                json=[
                    {
                        "hotelId": 1,
                        "hotelName": "Hotel Central",
                        "priceFrom": 300,  # total for the stay
                        "stars": 8.2,
                        "hasFreeCancellation": True,
                        "hotelPageUrl": "https://example.test/hotel/1",
                    }
                ],
            )
        if request.url.path == "/api/v2/static/hotels.json":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "kind": "hotel",
                        "location": {"geo": {"lat": 41.39, "lon": 2.16}},
                    }
                ],
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = _provider(handler)
    results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)

    assert len(results) == 1
    quote = results[0]
    assert quote.nightly_rate.amount == 100  # 300 / 3 nights
    assert quote.property_type == "hotel"
    assert quote.rating == 8.2
    assert quote.free_cancellation is True
    assert quote.distance_km is not None  # Barcelona centre is in the catalog
    assert quote.confidence == "observed"


def test_search_falls_back_gracefully_when_static_metadata_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/cache.json":
            return httpx.Response(200, json=[{"hotelId": 99, "priceFrom": 150}])
        return httpx.Response(200, json=[])  # no static metadata for hotel 99

    provider = _provider(handler)
    results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)

    assert len(results) == 1
    assert results[0].property_type == "hotel"  # documented default fallback
    assert results[0].distance_km is None  # can't compute without geo data


def test_search_returns_empty_for_zero_or_negative_nights():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make a request for an invalid date range")

    provider = _provider(handler)
    assert provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 4), adults=2) == []
    assert provider.search("barcelona", date(2027, 3, 7), date(2027, 3, 4), adults=2) == []


def test_search_skips_entries_with_no_price():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/cache.json":
            return httpx.Response(200, json=[{"hotelId": 1}, {"hotelId": 2, "priceFrom": 90}])
        return httpx.Response(200, json=[])

    provider = _provider(handler)
    results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)
    assert len(results) == 1


def test_results_sorted_cheapest_first():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/cache.json":
            return httpx.Response(
                200,
                json=[
                    {"hotelId": 1, "priceFrom": 300},
                    {"hotelId": 2, "priceFrom": 90},
                    {"hotelId": 3, "priceFrom": 210},
                ],
            )
        return httpx.Response(200, json=[])

    provider = _provider(handler)
    results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)
    rates = [q.nightly_rate.minor_units for q in results]
    assert rates == sorted(rates)


def test_haversine_zero_distance_for_same_point():
    assert _haversine_km(41.39, 2.16, 41.39, 2.16) == 0.0


def test_haversine_known_distance_barcelona_to_madrid():
    # Barcelona (41.3851, 2.1734) to Madrid (40.4168, -3.7038) is ~505 km great-circle.
    km = _haversine_km(41.3851, 2.1734, 40.4168, -3.7038)
    assert 480 <= km <= 520


def test_endpoints_are_the_documented_urls():
    assert CACHE_URL == "https://engine.hotellook.com/api/v2/cache.json"
    assert STATIC_HOTELS_URL == "https://engine.hotellook.com/api/v2/static/hotels.json"


class TestCatalogFallback:
    """Hotellook is confirmed permanently dead (shut down 2025-10-15,
    see this module's docstring) -- every live request currently fails,
    so search() must fall back to a catalog estimate rather than raise.
    """

    def test_falls_back_to_catalog_estimate_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="<html>404 Not Found</html>")

        provider = _provider(handler)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)

        assert len(results) == 1
        estimate = results[0]
        assert estimate.confidence == "city_median_estimate"
        assert estimate.source == "catalog_estimate"
        assert estimate.nightly_rate.amount > 0
        assert estimate.deep_link is None

    def test_estimate_leaves_unknown_attributes_unset_rather_than_guessed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        provider = _provider(handler)
        estimate = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)[0]
        assert estimate.rating is None
        assert estimate.distance_km is None
        assert estimate.free_cancellation is False

    def test_estimate_rating_and_distance_correctly_fail_stay_filters(self):
        from holiday_tracker.engine.filters import passes_filters
        from holiday_tracker.models import StayFilters

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        provider = _provider(handler)
        estimate = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)[0]

        # An unverified estimate must not silently satisfy a filter that
        # depends on data it doesn't actually have.
        assert not passes_filters(estimate, StayFilters(min_rating=5.0))
        assert not passes_filters(estimate, StayFilters(max_centre_km=10.0))
        assert not passes_filters(estimate, StayFilters(free_cancellation_only=True))
        # A filter that only needs the property type (which the estimate
        # does set to "hotel") still passes.
        assert passes_filters(estimate, StayFilters(exclude_hostels=True))

    def test_falls_back_on_5xx_after_retries_too(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        provider = _provider(handler)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)
        assert len(results) == 1
        assert results[0].confidence == "city_median_estimate"

    def test_unknown_city_returns_no_estimate(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        provider = _provider(handler)
        results = provider.search("nowhereville", date(2027, 3, 4), date(2027, 3, 7), adults=2)
        assert results == []

    def test_successful_live_response_does_not_use_the_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/cache.json":
                return httpx.Response(200, json=[{"hotelId": 1, "priceFrom": 90}])
            return httpx.Response(200, json=[])

        provider = _provider(handler)
        results = provider.search("barcelona", date(2027, 3, 4), date(2027, 3, 7), adults=2)
        assert len(results) == 1
        assert results[0].confidence == "observed"
        assert results[0].source == "hotellook"
