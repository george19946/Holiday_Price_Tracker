"""Tests for the Travelpayouts flight-calendar adapter, against a mocked
transport shaped like the documented API response -- no real network call.

See providers/travelpayouts.py's module docstring for the caveat that this
shape is not yet confirmed against a live token.
"""

from __future__ import annotations

import httpx

from holiday_tracker.providers.http import CachedHttpClient, ResponseCache
from holiday_tracker.providers.travelpayouts import (
    CALENDAR_URL,
    TravelpayoutsFlightProvider,
)


def _provider(handler, currency: str = "gbp") -> TravelpayoutsFlightProvider:
    transport = httpx.MockTransport(handler)
    http_client = CachedHttpClient(cache=ResponseCache(":memory:"), client=httpx.Client(transport=transport))
    return TravelpayoutsFlightProvider(http_client, token="test-token", currency=currency)


def test_fare_calendar_parses_documented_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/prices/calendar"
        assert request.url.params["origin"] == "LHR"
        assert request.url.params["destination"] == "BCN"
        assert request.url.params["depart_date"] == "2027-03"
        assert request.headers["X-Access-Token"] == "test-token"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "2027-03-04": {"price": 87, "airline": "FR"},
                    "2027-03-05": {"price": 102, "airline": "VY"},
                },
            },
        )

    provider = _provider(handler)
    calendar = provider.fare_calendar("LHR", "BCN", 2027, 3)

    assert calendar.origin == "LHR"
    assert calendar.destination_iata == "BCN"
    assert calendar.source == "travelpayouts"
    assert len(calendar.prices) == 2
    from datetime import date

    assert calendar.prices[date(2027, 3, 4)].amount == 87
    assert calendar.prices[date(2027, 3, 4)].currency == "GBP"


def test_fare_calendar_skips_null_and_malformed_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "2027-03-04": {"price": 87},
                    "2027-03-05": None,
                    "not-a-date": {"price": 50},
                }
            },
        )

    provider = _provider(handler)
    calendar = provider.fare_calendar("LHR", "BCN", 2027, 3)
    assert len(calendar.prices) == 1


def test_fare_calendar_handles_empty_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {}})

    provider = _provider(handler)
    calendar = provider.fare_calendar("LHR", "XXX", 2027, 3)
    assert calendar.prices == {}


def test_currency_is_passed_through_and_upper_cased_on_money():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["currency"] == "eur"
        return httpx.Response(200, json={"data": {"2027-03-04": {"price": 50}}})

    provider = _provider(handler, currency="eur")
    calendar = provider.fare_calendar("CDG", "BCN", 2027, 3)
    from datetime import date

    assert calendar.prices[date(2027, 3, 4)].currency == "EUR"


def test_repeated_call_is_served_from_cache():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"data": {}})

    transport = httpx.MockTransport(handler)
    http_client = CachedHttpClient(cache=ResponseCache(":memory:"), client=httpx.Client(transport=transport))
    provider = TravelpayoutsFlightProvider(http_client, token="tok")

    provider.fare_calendar("LHR", "BCN", 2027, 3)
    provider.fare_calendar("LHR", "BCN", 2027, 3)
    assert len(calls) == 1


def test_calendar_url_is_the_documented_endpoint():
    assert CALENDAR_URL == "https://api.travelpayouts.com/v1/prices/calendar"
