"""Adapter for the Hotellook (Travelpayouts) cached hotel-price API.

Free, token-gated sign-up — same Travelpayouts account as flights. Two
endpoints combine to produce a filterable StayQuote:
  - `/api/v2/cache.json` — cached nightly prices for hotels in a location.
  - `/api/v2/static/hotels.json` — per-hotel metadata (coordinates,
    property "kind") needed for the "no hostels" and "max distance from
    centre" stay filters, since the price endpoint alone doesn't carry it.

Rate limit: 60 requests/minute (see providers/http.py's TokenBucket) —
noticeably tighter than flights, which is exactly why the search engine
(phase 3) only prices stays for its narrowed-down shortlist of candidates
rather than for every flight-sweep result.

VERIFICATION NOTE: written against Travelpayouts' published documentation
(https://support.travelpayouts.com/hc/en-us/articles/115000343268-Hotels-data-API,
https://support.travelpayouts.com/hc/en-us/articles/203956133-Hotel-search-API)
but not exercised against a live token in this environment. Two things in
particular need confirming with a real token before this is trusted:
  1. The `location` parameter Hotellook's cache.json expects is its own
     numeric location id, not an arbitrary city name/slug -- catalog city
     ids (e.g. "barcelona") are passed straight through below as a
     starting point, and will need a real id mapping (bundled alongside
     cities.yaml, the same way IATA codes already are) once verified.
  2. The exact field names on each cache.json entry (`priceFrom`,
     `hotelId`, `stars`) and each static hotels.json entry (`id`, `kind`,
     `location.geo`) are the ones the docs describe, not yet confirmed
     against a live response.
If the real shape differs, only this file and its tests need to change —
the StayProvider protocol and everything downstream is unaffected.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from holiday_tracker.catalog.loader import load_cities
from holiday_tracker.models import Money, StayQuote
from holiday_tracker.providers.http import CachedHttpClient

CACHE_URL = "https://engine.hotellook.com/api/v2/cache.json"
STATIC_HOTELS_URL = "https://engine.hotellook.com/api/v2/static/hotels.json"

# Cached hotel prices move less predictably than flight fares, but the hard
# 60 req/min limit makes staying cache-friendly more important than
# freshness here.
STAY_CACHE_TTL_SECONDS = 12 * 60 * 60
# Hotel metadata (coordinates, property type) barely changes; cache it long.
STATIC_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class HotellookStayProvider:
    """StayProvider backed by Hotellook's cached price search, enriched with
    static per-hotel metadata (coordinates, property type) for filtering."""

    def __init__(self, http_client: CachedHttpClient, token: str, currency: str = "gbp") -> None:
        self._http = http_client
        self._token = token
        self._currency = currency

    def _static_metadata(self, city_id: str) -> dict[int, dict]:
        payload = self._http.get_json(
            STATIC_HOTELS_URL,
            params={"locationId": city_id},
            cache_ttl_seconds=STATIC_CACHE_TTL_SECONDS,
            cache_key=f"hotels-static:{city_id}",
        )
        if not isinstance(payload, list):
            return {}
        metadata = {}
        for hotel in payload:
            hotel_id = hotel.get("id")
            if hotel_id is not None:
                metadata[hotel_id] = hotel
        return metadata

    def search(
        self, city_id: str, check_in: date, check_out: date, adults: int, limit: int = 20
    ) -> list[StayQuote]:
        nights = (check_out - check_in).days
        if nights <= 0:
            return []

        payload = self._http.get_json(
            CACHE_URL,
            params={
                "location": city_id,
                "checkIn": check_in.isoformat(),
                "checkOut": check_out.isoformat(),
                "adults": adults,
                "currency": self._currency,
                "limit": limit,
                "token": self._token,
            },
            cache_ttl_seconds=STAY_CACHE_TTL_SECONDS,
            cache_key=f"hotels:{city_id}:{check_in}:{check_out}:{adults}:{self._currency}:{limit}",
        )
        if not isinstance(payload, list):
            return []

        static_metadata = self._static_metadata(city_id)
        city_centre = load_cities().get(city_id)
        observed_at = datetime.now(UTC)

        quotes: list[StayQuote] = []
        for entry in payload:
            price_total = entry.get("priceFrom") or entry.get("price")
            if price_total is None:
                continue
            nightly = float(price_total) / nights

            hotel_meta = static_metadata.get(entry.get("hotelId"), {})
            property_type = str(hotel_meta.get("kind") or entry.get("propertyType") or "hotel")

            distance_km = None
            geo = hotel_meta.get("location", {}).get("geo") if hotel_meta else None
            if geo and city_centre is not None:
                distance_km = round(
                    _haversine_km(
                        city_centre.centre_lat,
                        city_centre.centre_lon,
                        float(geo["lat"]),
                        float(geo["lon"]),
                    ),
                    1,
                )

            quotes.append(
                StayQuote(
                    city_id=city_id,
                    check_in=check_in,
                    check_out=check_out,
                    nightly_rate=Money.from_major(nightly, self._currency.upper()),
                    property_type=property_type,
                    rating=entry.get("stars"),
                    distance_km=distance_km,
                    free_cancellation=bool(entry.get("hasFreeCancellation", False)),
                    observed_at=observed_at,
                    source="hotellook",
                    deep_link=entry.get("hotelPageUrl"),
                    confidence="observed",
                )
            )

        quotes.sort(key=lambda q: q.nightly_rate.minor_units)
        return quotes
