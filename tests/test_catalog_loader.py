"""Unit tests for holiday_tracker.catalog.loader against the real bundled YAML data.

These intentionally exercise the actual cities.yaml/regions.yaml/spend.yaml
shipped with the package (not fixtures) — the loader's own validation is
what guarantees every region only references real cities and every city
has a spend entry, so a bad edit to the data fails CI immediately.
"""

import pytest

from holiday_tracker.catalog.loader import (
    load_cities,
    load_nightly_rates,
    load_regions,
    load_spend,
    resolve_destination,
)
from holiday_tracker.models import SpendStyle


def test_load_cities_returns_populated_dict():
    cities = load_cities()
    assert len(cities) >= 40
    barcelona = cities["barcelona"]
    assert barcelona.name == "Barcelona"
    assert barcelona.country == "Spain"
    assert "BCN" in barcelona.iata
    assert -90 <= barcelona.centre_lat <= 90
    assert -180 <= barcelona.centre_lon <= 180


def test_load_regions_only_reference_known_cities():
    cities = load_cities()
    regions = load_regions()
    assert "western_europe" in regions
    for region in regions.values():
        assert region.city_ids, f"region {region.id} has no cities"
        for city_id in region.city_ids:
            assert city_id in cities


def test_every_city_has_a_spend_entry():
    cities = load_cities()
    spend = load_spend()
    missing = set(cities) - set(spend)
    assert not missing, f"cities missing a spend.yaml entry: {missing}"


def test_spend_daily_total_and_style_multiplier():
    spend = load_spend()
    barcelona_spend = spend["barcelona"]
    assert barcelona_spend.daily_total == pytest.approx(
        barcelona_spend.food + barcelona_spend.local_transport + barcelona_spend.activities
    )
    normal = barcelona_spend.daily_total_for_style(SpendStyle.normal)
    thrifty = barcelona_spend.daily_total_for_style(SpendStyle.thrifty)
    comfortable = barcelona_spend.daily_total_for_style(SpendStyle.comfortable)
    assert thrifty < normal < comfortable
    assert normal == pytest.approx(barcelona_spend.daily_total)


class TestResolveDestination:
    def test_resolves_city_id(self):
        assert resolve_destination("barcelona") == ["barcelona"]

    def test_resolves_city_name_case_insensitively(self):
        assert resolve_destination("Barcelona") == ["barcelona"]
        assert resolve_destination("BARCELONA") == ["barcelona"]

    def test_resolves_region_id(self):
        cities = resolve_destination("western_europe")
        assert "paris" in cities
        assert "barcelona" not in cities  # Barcelona is southern_europe, not western

    def test_resolves_region_name_with_spaces_case_insensitively(self):
        assert resolve_destination("Western Europe") == resolve_destination("western_europe")
        assert resolve_destination("western europe") == resolve_destination("western_europe")

    def test_resolves_balkans_region_from_the_readme_example(self):
        cities = resolve_destination("Balkans")
        assert "belgrade" in cities

    def test_resolves_southern_france_region(self):
        cities = resolve_destination("Southern France")
        assert set(cities) == {"nice", "marseille", "montpellier", "toulouse"}

    def test_unknown_destination_raises(self):
        with pytest.raises(ValueError, match="unknown destination"):
            resolve_destination("Atlantis")


class TestLoadNightlyRates:
    def test_every_city_has_a_nightly_rate_entry(self):
        cities = load_cities()
        rates = load_nightly_rates()
        missing = set(cities) - set(rates)
        assert not missing, f"cities missing a nightly_rate.yaml entry: {missing}"

    def test_for_style_applies_the_shared_multipliers(self):
        rates = load_nightly_rates()
        barcelona = rates["barcelona"]
        normal = barcelona.for_style(SpendStyle.normal)
        thrifty = barcelona.for_style(SpendStyle.thrifty)
        comfortable = barcelona.for_style(SpendStyle.comfortable)
        assert thrifty < normal < comfortable
        assert normal == pytest.approx(barcelona.normal)

    def test_rates_are_positive_and_have_a_currency(self):
        for rate in load_nightly_rates().values():
            assert rate.normal > 0
            assert rate.currency
