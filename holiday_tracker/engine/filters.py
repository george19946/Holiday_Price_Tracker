"""Stay filter predicates: does one StayQuote satisfy a StayFilters?

Kept independent from ranking (engine/rank.py) so "what got filtered out
and why" can be inspected on its own — both for testing and because the
near-miss annotator needs to know exactly which single filter was binding
in order to suggest relaxing it.
"""

from __future__ import annotations

from holiday_tracker.models import StayFilters, StayQuote

# Property "kind" metadata is inconsistent across free-tier sources, so a
# name-based denylist is a documented second line of defence for "no
# hostels" (see the project plan's honest-limitations section and
# providers/hotellook.py).
_HOSTEL_KEYWORDS = ("hostel", "dorm", "backpacker")


def is_hostel_like(stay: StayQuote) -> bool:
    property_type = stay.property_type.lower()
    return any(keyword in property_type for keyword in _HOSTEL_KEYWORDS)


def binding_filter(stay: StayQuote, filters: StayFilters) -> str | None:
    """Which single filter field causes `stay` to fail `filters`, checked in
    a fixed order, or None if it passes everything.

    A constraint the provider can't corroborate (no rating reported, no
    distance reported) is treated as *failing* that constraint — a stated
    preference like "min rating 7.5" should not silently accept a stay we
    have no rating for.
    """
    if filters.exclude_hostels and is_hostel_like(stay):
        return "exclude_hostels"
    if filters.min_rating is not None and (
        stay.rating is None or stay.rating < filters.min_rating
    ):
        return "min_rating"
    if filters.max_centre_km is not None and (
        stay.distance_km is None or stay.distance_km > filters.max_centre_km
    ):
        return "max_centre_km"
    if filters.free_cancellation_only and not stay.free_cancellation:
        return "free_cancellation_only"
    return None


def passes_filters(stay: StayQuote, filters: StayFilters) -> bool:
    return binding_filter(stay, filters) is None


def cheapest_passing(stays: list[StayQuote], filters: StayFilters) -> StayQuote | None:
    """The cheapest stay satisfying every filter, from a list assumed to be
    roughly cheapest-first (as StayProvider.search returns), or None if
    nothing in the list satisfies them all."""
    for stay in stays:
        if passes_filters(stay, filters):
            return stay
    return None
