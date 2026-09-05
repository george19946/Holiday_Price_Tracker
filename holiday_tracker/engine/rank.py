"""Near-miss annotation: for a package that doesn't fit the budget, what is
the single cheapest change that would close (or narrow) the gap?

Every counterfactual here is computed against data the search (engine/
search.py) already fetched in stages 1-2 — no extra provider requests are
spent answering "what if". Three axes are considered, and the biggest
saving among the ones that apply wins:

  - dates:        the same destination, priced on different dates that
                   also made it into the results.
  - destination:  the same dates, a different destination that also made
                   it into the results.
  - stay filter:  the same destination and dates, but with the single
                   binding stay filter loosened one notch (e.g. allow a
                   hostel, drop the minimum rating by a point), re-picking
                   the cheapest stay that then qualifies from the same raw
                   stay list already fetched for that candidate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from holiday_tracker.engine.filters import cheapest_passing
from holiday_tracker.engine.pricing import convert, price_accommodation
from holiday_tracker.models import Money, Package, SearchSpec, StayFilters, StayQuote

_FILTER_DESCRIPTIONS: dict[str, str] = {
    "exclude_hostels": "allow a hostel-type stay",
    "min_rating": "accept a slightly lower rating",
    "max_centre_km": "stay a little further from the centre",
    "free_cancellation_only": "accept a non-refundable rate",
}


@dataclass(frozen=True)
class Relaxation:
    """One suggested change that would move a near-miss package closer to
    (or under) budget, and by how much."""

    axis: str  # "dates" | "destination" | "stay:<field>"
    description: str
    new_total: Money
    saving: Money
    closes_gap: bool


def _cheapest_other_matching(
    packages: list[Package], anchor: Package, group_key: Callable[[Package], object]
) -> Package | None:
    """The cheapest package (other than `anchor` itself) that shares
    `group_key(anchor)`'s value and costs strictly less than `anchor`."""
    key = group_key(anchor)
    cheaper = [
        p
        for p in packages
        if p is not anchor
        and group_key(p) == key
        and p.total_cost.minor_units < anchor.total_cost.minor_units
    ]
    if not cheaper:
        return None
    return min(cheaper, key=lambda p: p.total_cost.minor_units)


def _format_date_change(anchor: Package, alternative: Package) -> str:
    return (
        f"fly {alternative.depart_date:%a %d %b} instead of {anchor.depart_date:%a %d %b}"
        f" ({alternative.nights} night{'s' if alternative.nights != 1 else ''})"
    )


def _format_destination_name(city_id: str) -> str:
    return city_id.replace("_", " ").title()


def _loosened_stay_filters(filters: StayFilters) -> list[tuple[str, StayFilters]]:
    """One-notch-looser variants of `filters`, one per currently-active
    constraint, paired with which field was loosened."""
    variants: list[tuple[str, StayFilters]] = []
    if filters.exclude_hostels:
        variants.append(
            ("exclude_hostels", filters.model_copy(update={"exclude_hostels": False}))
        )
    if filters.min_rating is not None:
        variants.append(
            (
                "min_rating",
                filters.model_copy(update={"min_rating": max(0.0, filters.min_rating - 1.0)}),
            )
        )
    if filters.max_centre_km is not None:
        variants.append(
            (
                "max_centre_km",
                filters.model_copy(update={"max_centre_km": filters.max_centre_km * 1.5}),
            )
        )
    if filters.free_cancellation_only:
        variants.append(
            ("free_cancellation_only", filters.model_copy(update={"free_cancellation_only": False}))
        )
    return variants


def _suggest_stay_relaxation(
    anchor: Package,
    spec: SearchSpec,
    raw_stays: dict[tuple[str, object, object], list[StayQuote]],
) -> Relaxation | None:
    if anchor.stay is None:
        return None
    key = (anchor.destination_city_id, anchor.depart_date, anchor.return_date)
    candidates = raw_stays.get(key)
    if not candidates:
        return None

    best: Relaxation | None = None
    for field_name, loosened in _loosened_stay_filters(spec.stay_filters):
        alt_stay = cheapest_passing(candidates, loosened)
        if alt_stay is None:
            continue
        if alt_stay.nightly_rate.minor_units >= anchor.stay.nightly_rate.minor_units:
            continue  # the loosened filter didn't actually unlock anything cheaper

        new_accommodation_cost = convert(
            price_accommodation(alt_stay.nightly_rate, anchor.nights, spec.rooms_needed),
            spec.budget.currency,
        )
        saving = anchor.accommodation_cost - new_accommodation_cost
        new_total = anchor.total_cost - saving
        candidate_relaxation = Relaxation(
            axis=f"stay:{field_name}",
            description=_FILTER_DESCRIPTIONS[field_name],
            new_total=new_total,
            saving=saving,
            closes_gap=new_total <= spec.budget,
        )
        if best is None or saving.minor_units > best.saving.minor_units:
            best = candidate_relaxation

    return best


def suggest_relaxation(
    anchor: Package,
    all_packages: list[Package],
    spec: SearchSpec,
    raw_stays: dict[tuple[str, object, object], list[StayQuote]],
) -> Relaxation | None:
    """The single cheapest-to-apply change that would reduce `anchor`'s
    total cost, chosen among whichever of the three axes has data to
    support it. Returns None if nothing in the already-fetched data offers
    any saving at all.
    """
    candidates: list[Relaxation] = []

    alt_dates = _cheapest_other_matching(
        all_packages, anchor, lambda p: p.destination_city_id
    )
    if alt_dates is not None:
        saving = anchor.total_cost - alt_dates.total_cost
        candidates.append(
            Relaxation(
                axis="dates",
                description=_format_date_change(anchor, alt_dates),
                new_total=alt_dates.total_cost,
                saving=saving,
                closes_gap=alt_dates.fits_budget,
            )
        )

    alt_destination = _cheapest_other_matching(
        all_packages, anchor, lambda p: (p.depart_date, p.return_date)
    )
    if alt_destination is not None:
        saving = anchor.total_cost - alt_destination.total_cost
        candidates.append(
            Relaxation(
                axis="destination",
                description=(
                    f"go to {_format_destination_name(alt_destination.destination_city_id)} "
                    "instead"
                ),
                new_total=alt_destination.total_cost,
                saving=saving,
                closes_gap=alt_destination.fits_budget,
            )
        )

    stay_relaxation = _suggest_stay_relaxation(anchor, spec, raw_stays)
    if stay_relaxation is not None:
        candidates.append(stay_relaxation)

    if not candidates:
        return None
    return max(candidates, key=lambda r: r.saving.minor_units)


def describe_relaxation(anchor: Package, relaxation: Relaxation) -> str:
    """Render a Relaxation as the one-line message described in the project
    plan, e.g. "£540 — £40 over budget. Fly Fri instead of Thu: saves £60.
    Closes the gap."."""
    over_by = anchor.over_budget_by
    header = f"{anchor.total_cost}"
    if over_by is not None:
        header += f" — {over_by} over budget"
    verb = relaxation.description[0].upper() + relaxation.description[1:]
    tail = "closes the gap." if relaxation.closes_gap else "still over budget."
    return f"{header}. {verb}: saves {relaxation.saving}. {tail.capitalize()}"
