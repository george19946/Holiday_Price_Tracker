"""Loads the bundled destination and cost-of-living catalogues.

These YAML files (cities.yaml, regions.yaml, spend.yaml) are hand-maintained
data, not fetched from any API — there is no free live destination-metadata
or cost-of-living service (see the project README). Correcting or extending
an entry is a one-line YAML edit, not a code change.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from holiday_tracker.models import SPEND_STYLE_MULTIPLIERS, SpendStyle

_PACKAGE = "holiday_tracker.catalog"


@dataclass(frozen=True)
class City:
    id: str
    name: str
    country: str
    iata: list[str]
    centre_lat: float
    centre_lon: float
    currency: str


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    city_ids: list[str]


@dataclass(frozen=True)
class DailySpend:
    """Estimated daily spend per person at the "normal" style tier."""

    city_id: str
    food: float
    local_transport: float
    activities: float
    currency: str

    @property
    def daily_total(self) -> float:
        return self.food + self.local_transport + self.activities

    def daily_total_for_style(self, style: SpendStyle) -> float:
        return self.daily_total * SPEND_STYLE_MULTIPLIERS[style]


@dataclass(frozen=True)
class NightlyRateEstimate:
    """Estimated nightly rate for a standard double room at the "normal"
    style tier -- used only as a fallback when live hotel pricing is
    unavailable (see providers/hotellook.py)."""

    city_id: str
    normal: float
    currency: str

    def for_style(self, style: SpendStyle) -> float:
        return self.normal * SPEND_STYLE_MULTIPLIERS[style]


def _read_yaml(filename: str) -> dict[str, Any]:
    text = resources.files(_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


@functools.lru_cache(maxsize=1)
def load_cities() -> dict[str, City]:
    raw = _read_yaml("cities.yaml")
    cities: dict[str, City] = {}
    for city_id, fields in raw.items():
        cities[city_id] = City(
            id=city_id,
            name=fields["name"],
            country=fields["country"],
            iata=list(fields["iata"]),
            centre_lat=float(fields["centre_lat"]),
            centre_lon=float(fields["centre_lon"]),
            currency=fields.get("currency", "EUR"),
        )
    return cities


@functools.lru_cache(maxsize=1)
def load_regions() -> dict[str, Region]:
    raw = _read_yaml("regions.yaml")
    cities = load_cities()
    regions: dict[str, Region] = {}
    for region_id, fields in raw.items():
        city_ids = list(fields["cities"])
        unknown = [c for c in city_ids if c not in cities]
        if unknown:
            raise ValueError(f"region {region_id!r} references unknown cities: {unknown}")
        regions[region_id] = Region(id=region_id, name=fields["name"], city_ids=city_ids)
    return regions


@functools.lru_cache(maxsize=1)
def load_spend() -> dict[str, DailySpend]:
    raw = _read_yaml("spend.yaml")
    cities = load_cities()
    spend: dict[str, DailySpend] = {}
    for city_id, fields in raw.items():
        if city_id not in cities:
            raise ValueError(f"spend.yaml references unknown city {city_id!r}")
        spend[city_id] = DailySpend(
            city_id=city_id,
            food=float(fields["food"]),
            local_transport=float(fields["local_transport"]),
            activities=float(fields["activities"]),
            currency=fields.get("currency", cities[city_id].currency),
        )
    return spend


@functools.lru_cache(maxsize=1)
def load_nightly_rates() -> dict[str, NightlyRateEstimate]:
    raw = _read_yaml("nightly_rate.yaml")
    cities = load_cities()
    rates: dict[str, NightlyRateEstimate] = {}
    for city_id, fields in raw.items():
        if city_id not in cities:
            raise ValueError(f"nightly_rate.yaml references unknown city {city_id!r}")
        rates[city_id] = NightlyRateEstimate(
            city_id=city_id,
            normal=float(fields["normal"]),
            currency=fields.get("currency", cities[city_id].currency),
        )
    return rates


def resolve_destination(query: str) -> list[str]:
    """Resolve a user-supplied destination string to a list of city ids.

    Accepts (case-insensitively) a city id or name, or a region id or name,
    e.g. "Barcelona", "barcelona", "Western Europe", or "western_europe".
    Raises ValueError with no partial results if nothing matches, so callers
    don't have to guess whether an empty list means "no match" or "matched
    an empty region".
    """
    stripped = query.strip()
    normalized_id = stripped.lower().replace(" ", "_")
    cities = load_cities()
    regions = load_regions()

    if normalized_id in cities:
        return [normalized_id]
    if normalized_id in regions:
        return list(regions[normalized_id].city_ids)

    lowered = stripped.lower()
    for city in cities.values():
        if city.name.lower() == lowered:
            return [city.id]
    for region in regions.values():
        if region.name.lower() == lowered:
            return list(region.city_ids)

    raise ValueError(f"unknown destination: {query!r}")
