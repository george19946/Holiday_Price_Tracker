"""Adapter for the Hotellook (Travelpayouts) cached hotel-price API --
plus the catalog-estimate fallback this module now needs because that API
no longer exists.

CONFIRMED DEAD (2026-09-05): Hotellook -- the consumer brand, its
affiliate program, and every endpoint under engine.hotellook.com --
shut down permanently on 2025-10-15. Every path on that host, including
the bare root, now returns a CloudFront-fronted 404 with no real origin
behind it; Travelpayouts' own closure FAQ states plainly that "no other
hotel brand offers API to Travelpayouts partners" as a replacement. This
was verified live against a real token: requests to /api/v2/cache.json
with several different `location` values all 404'd identically, and so
did the bare domain root -- ruling out a parameter mistake. The other
hotel APIs surveyed (RateHawk, Booking.com) require manual partner
approval with no self-serve token, and Booking.com's terms additionally
prohibit AI-system use without their prior written approval. There is
currently no free, self-serve, live hotel-price API to integrate.

So `search()` still tries the real endpoints first (in case Travelpayouts
ever brings hotel data back, or a token gets access to something this
environment couldn't reach), but on failure falls back to a single
estimated StayQuote from the bundled catalog (catalog/nightly_rate.yaml,
in the same spirit as catalog/spend.yaml's daily-spend estimates).
Only the *price* in that fallback is a genuine, if rough, estimate --
property type, rating, distance, and cancellation policy are all unknown,
so they're left unset rather than guessed. A stay filter that depends on
one of those (min rating, max distance, free cancellation) correctly
rejects the estimate instead of silently passing it (see
engine/filters.py's binding_filter) -- meaning a filtered real search can
legitimately show fewer results, or none, while this outage lasts. That's
the honest behaviour, not a bug.

The original design (still exercised whenever the live endpoints work)
combines two endpoints:
  - `/api/v2/cache.json` — cached nightly prices for hotels in a location.
  - `/api/v2/static/hotels.json` — per-hotel metadata (coordinates,
    property "kind") needed for the "no hostels" and "max distance from
    centre" stay filters, since the price endpoint alone doesn't carry it.
That part was written against Travelpayouts' published documentation but
was never exercised against a live response before the service turned out
to be gone -- so the field names and the `location` parameter's expected
format (documented as a numeric location id, not a city slug) remain
unconfirmed. If Hotellook-equivalent data ever becomes available again
under this shape, only this file and its tests should need to change.

Rate limit: 60 requests/minute (see providers/http.py's TokenBucket) --
noticeably tighter than flights, which is exactly why the search engine
(phase 3) only prices stays for its narrowed-down shortlist of candidates
rather than for every flight-sweep result.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from holiday_tracker.catalog.loader import load_cities, load_nightly_rates
from holiday_tracker.models import Money, SpendStyle, StayQuote
from holiday_tracker.providers.http import CachedHttpClient, ProviderError

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
    """StayProvider backed by Hotellook's cached price search when
    reachable, falling back to a catalog nightly-rate estimate when it
    isn't -- see this module's docstring for why that's currently always."""

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

    def _live_search(
        self, city_id: str, check_in: date, check_out: date, adults: int, nights: int, limit: int
    ) -> list[StayQuote]:
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

    def _estimate(self, city_id: str, check_in: date, check_out: date) -> StayQuote | None:
        """Fallback used when live hotel pricing fails. Only the price is
        a genuine (if rough) estimate; property type, rating, distance,
        and cancellation policy are all unknown and left unset, so a
        stay filter that depends on one of them correctly rejects this
        estimate (see engine/filters.py) rather than silently passing it.
        """
        rate = load_nightly_rates().get(city_id)
        if rate is None:
            return None
        return StayQuote(
            city_id=city_id,
            check_in=check_in,
            check_out=check_out,
            nightly_rate=Money.from_major(rate.for_style(SpendStyle.normal), rate.currency),
            property_type="hotel",
            rating=None,
            distance_km=None,
            free_cancellation=False,
            observed_at=datetime.now(UTC),
            source="catalog_estimate",
            deep_link=None,
            confidence="city_median_estimate",
        )

    def search(
        self, city_id: str, check_in: date, check_out: date, adults: int, limit: int = 20
    ) -> list[StayQuote]:
        nights = (check_out - check_in).days
        if nights <= 0:
            return []

        try:
            return self._live_search(city_id, check_in, check_out, adults, nights, limit)
        except ProviderError:
            estimate = self._estimate(city_id, check_in, check_out)
            return [estimate] if estimate is not None else []
