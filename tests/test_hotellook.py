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
    http_client = CachedHttpClient(cache=ResponseCache(":memory:"), client=httpx.Client(transport=transport))
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
