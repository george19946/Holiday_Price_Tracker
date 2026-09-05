"""Tests for the Travelpayouts flight adapter, against a mocked transport
shaped like the *actual* responses confirmed by live testing against a
real token on 2026-09-05 (see providers/travelpayouts.py's module
docstring for the full story) -- not the originally assumed shape, which
turned out to be wrong in an important way (the calendar endpoint ignores
its own depart_date parameter).
"""

from __future__ import annotations

from datetime import date

import httpx

from holiday_tracker.providers.http import CachedHttpClient, ResponseCache
from holiday_tracker.providers.travelpayouts import (
    CALENDAR_URL,
    PRICES_FOR_DATES_URL,
    TravelpayoutsFlightProvider,
)


def _provider(handler, currency: str = "gbp") -> TravelpayoutsFlightProvider:
    transport = httpx.MockTransport(handler)
    http_client = CachedHttpClient(
        cache=ResponseCache(":memory:"), client=httpx.Client(transport=transport)
    )
    return TravelpayoutsFlightProvider(http_client, token="test-token", currency=currency)


class TestFareCalendar:
    def test_parses_the_confirmed_real_response_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/prices/calendar"
            assert request.url.params["origin"] == "LHR"
            assert request.url.params["destination"] == "BCN"
            assert request.headers["X-Access-Token"] == "test-token"
            # No depart_date assertion: the real endpoint ignores it, so
            # this adapter no longer sends one -- see the module docstring.
            assert "depart_date" not in request.url.params
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "2026-09-05": {
                            "origin": "LON",
                            "destination": "BCN",
                            "airline": "VY",
                            "departure_at": "2026-09-05T20:50:00+01:00",
                            "return_at": "2026-09-11T18:35:00+02:00",
                            "price": 123,
                            "transfers": 0,
                        },
                        "2026-09-06": {
                            "departure_at": "2026-09-06T09:55:00+01:00",
                            "return_at": "2026-09-10T07:30:00+02:00",
                            "price": 114,
                        },
                    },
                },
            )

        provider = _provider(handler)
        fares = provider.fare_calendar("LHR", "BCN")

        assert len(fares) == 2
        by_depart = {f.depart_date: f for f in fares}
        assert by_depart[date(2026, 9, 5)].return_date == date(2026, 9, 11)
        assert by_depart[date(2026, 9, 5)].price.amount == 123
        assert by_depart[date(2026, 9, 5)].price.currency == "GBP"
        assert by_depart[date(2026, 9, 5)].source == "travelpayouts"
        # The calendar endpoint has no booking link in the real response.
        assert by_depart[date(2026, 9, 5)].deep_link is None

    def test_skips_entries_missing_price_or_dates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "a": {
                            "price": 87,
                            "departure_at": "2027-03-04T10:00:00Z",
                            "return_at": "2027-03-07T10:00:00Z",
                        },
                        "b": None,
                        "c": {"price": 50},  # missing departure_at/return_at
                        "d": {"departure_at": "2027-03-04T10:00:00Z", "return_at": "2027-03-07T10:00:00Z"},
                    }
                },
            )

        provider = _provider(handler)
        fares = provider.fare_calendar("LHR", "BCN")
        assert len(fares) == 1

    def test_handles_empty_data(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "data": {}})

        provider = _provider(handler)
        assert provider.fare_calendar("LHR", "XXX") == []

    def test_currency_is_passed_through_and_upper_cased_on_money(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["currency"] == "eur"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "a": {
                            "price": 50,
                            "departure_at": "2027-03-04T10:00:00Z",
                            "return_at": "2027-03-07T10:00:00Z",
                        }
                    }
                },
            )

        provider = _provider(handler, currency="eur")
        fares = provider.fare_calendar("CDG", "BCN")
        assert fares[0].price.currency == "EUR"

    def test_repeated_call_is_served_from_cache(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"data": {}})

        provider = _provider(handler)
        provider.fare_calendar("LHR", "BCN")
        provider.fare_calendar("LHR", "BCN")
        assert len(calls) == 1

    def test_url_is_the_documented_endpoint(self):
        assert CALENDAR_URL == "https://api.travelpayouts.com/v1/prices/calendar"


class TestCheapestFareInMonth:
    def test_parses_the_confirmed_real_response_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/aviasales/v3/prices_for_dates"
            assert request.url.params["origin"] == "LHR"
            assert request.url.params["destination"] == "BCN"
            assert request.url.params["departure_at"] == "2027-03"
            assert request.url.params["one_way"] == "false"
            assert request.url.params["token"] == "test-token"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "flight_number": "7643",
                            "link": "/search/LHR2303BCN26031?t=abc",
                            "departure_at": "2027-03-23T20:50:00Z",
                            "return_at": "2027-03-26T18:45:00+01:00",
                            "airline": "VY",
                            "price": 118,
                            "gate": "Biletix",
                        }
                    ],
                    "currency": "gbp",
                    "success": True,
                },
            )

        provider = _provider(handler)
        fare = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)

        assert fare is not None
        assert fare.depart_date == date(2027, 3, 23)
        assert fare.return_date == date(2027, 3, 26)
        assert fare.price.amount == 118
        assert fare.price.currency == "GBP"
        assert fare.source == "travelpayouts"
        # The relative "link" is resolved into an openable URL.
        assert fare.deep_link == "https://www.aviasales.com/search/LHR2303BCN26031?t=abc"

    def test_returns_none_when_no_fares_available(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [], "success": True})

        provider = _provider(handler)
        assert provider.cheapest_fare_in_month("LHR", "XXX", 2027, 3) is None

    def test_returns_none_when_data_is_null(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": None, "success": False})

        provider = _provider(handler)
        assert provider.cheapest_fare_in_month("LHR", "XXX", 2027, 3) is None

    def test_returns_none_when_entry_is_missing_required_fields(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"price": 50}]})

        provider = _provider(handler)
        assert provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3) is None

    def test_absolute_link_is_passed_through_unchanged(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "link": "https://example.test/already-absolute",
                            "departure_at": "2027-03-04T10:00:00Z",
                            "return_at": "2027-03-07T10:00:00Z",
                            "price": 100,
                        }
                    ]
                },
            )

        provider = _provider(handler)
        fare = provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        assert fare.deep_link == "https://example.test/already-absolute"

    def test_repeated_call_is_served_from_cache(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"data": []})

        provider = _provider(handler)
        provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        assert len(calls) == 1

    def test_different_months_are_cached_separately(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["departure_at"])
            return httpx.Response(200, json={"data": []})

        provider = _provider(handler)
        provider.cheapest_fare_in_month("LHR", "BCN", 2027, 3)
        provider.cheapest_fare_in_month("LHR", "BCN", 2027, 4)
        assert calls == ["2027-03", "2027-04"]

    def test_url_is_the_documented_endpoint(self):
        assert PRICES_FOR_DATES_URL == "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def test_request_count_is_forwarded_from_the_http_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    provider = _provider(handler)
    assert provider.request_count == 0
    provider.fare_calendar("LHR", "BCN")
    assert provider.request_count == 1
