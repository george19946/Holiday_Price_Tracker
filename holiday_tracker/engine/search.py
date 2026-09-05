"""The three-stage search funnel described in the project plan:

  1. Wide flight sweep (sweep_flights) — collect whatever concrete
     round-trip fares a provider actually has cached for each route (one
     fare_calendar() call per route, since a real free-tier provider's
     cached fares are a sparse, rolling set rather than a per-day grid for
     a chosen month — see providers/base.py and providers/travelpayouts.py
     for what live testing against a real token confirmed), plus a
     genuinely month-scoped cheapest-fare fallback for each month the
     DateRule spans. Each fare is checked against the DateRule directly
     (dates.matches_date_rule) rather than generated and priced.
  2. Shortlist (shortlist_candidates) — rank stage 1's grid by flight price
     alone (a reasonable proxy: cheap flights leave the most budget
     headroom for accommodation) and keep only the cheapest few.
  3. Stay pricing + assembly (price_stays_and_assemble) — fetch real stay
     prices for only the shortlisted candidates, keep the cheapest one
     passing the stay filters, and build a fully priced Package.

run_search() wires all three stages together for one SearchSpec. Each
stage is a separate, independently testable function so a change to one
(e.g. a smarter shortlist heuristic later) doesn't require touching the
others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from holiday_tracker.catalog.loader import load_cities, load_spend, resolve_destination
from holiday_tracker.dates import departure_months, matches_date_rule
from holiday_tracker.engine.filters import cheapest_passing
from holiday_tracker.engine.pricing import build_package
from holiday_tracker.models import Candidate, FlightQuote, Package, SearchSpec, StayQuote
from holiday_tracker.providers.base import CalendarFare, FlightProvider, StayProvider

DEFAULT_SHORTLIST_SIZE = 25

# A destination/date-range key for grouping candidates and caching stay
# lookups — stay price doesn't depend on which origin airport was flown
# from, so this lets several origins share one stay search.
_StayKey = tuple[str, date, date]


@dataclass
class SearchResults:
    """Everything a search run produced: the priced packages, and the raw
    (unfiltered) stay lists behind them — kept around so the near-miss
    annotator (engine/rank.py) can explore "what if a stay filter were
    relaxed" without spending another request.
    """

    spec: SearchSpec
    packages: list[Package] = field(default_factory=list)
    raw_stays: dict[_StayKey, list[StayQuote]] = field(default_factory=dict)
    flight_requests_made: int = 0

    @property
    def feasible(self) -> list[Package]:
        """Packages that fit the budget and satisfy every stay filter,
        cheapest first."""
        return sorted(
            (p for p in self.packages if p.fits_budget),
            key=lambda p: p.total_cost.minor_units,
        )

    @property
    def near_misses(self) -> list[Package]:
        """Packages that satisfy every stay filter but exceed the budget,
        cheapest first."""
        return sorted(
            (p for p in self.packages if not p.fits_budget),
            key=lambda p: p.total_cost.minor_units,
        )


def estimate_flight_requests(spec: SearchSpec, city_ids: list[str]) -> int:
    """How many flight requests sweep_flights will make for this spec, so a
    run can report (or guard on) its cost before spending any of the
    free-tier quota on it: one fare_calendar() call per route, plus one
    cheapest_fare_in_month() call per route per month the DateRule spans."""
    cities = load_cities()
    months = departure_months(spec.date_rule)
    airport_count = sum(len(cities[city_id].iata) for city_id in city_ids)
    routes = len(spec.origins) * airport_count
    return routes * (1 + len(months))


def _candidate_from_fare(
    origin: str, city_id: str, destination_iata: str, fare: CalendarFare
) -> Candidate:
    return Candidate(
        origin=origin,
        destination_city_id=city_id,
        destination_iata=destination_iata,
        depart_date=fare.depart_date,
        return_date=fare.return_date,
        nights=(fare.return_date - fare.depart_date).days,
        flight=FlightQuote(
            origin=origin,
            destination_iata=destination_iata,
            depart_date=fare.depart_date,
            return_date=fare.return_date,
            price=fare.price,
            observed_at=fare.observed_at,
            source=fare.source,
            deep_link=fare.deep_link,
        ),
    )


def sweep_flights(
    spec: SearchSpec, city_ids: list[str], flight_provider: FlightProvider
) -> list[Candidate]:
    """Stage 1: collect whatever concrete round-trip fares the provider
    actually has cached for each route -- one fare_calendar() call (its
    sparse, rolling set of currently-cheap fares) plus one
    cheapest_fare_in_month() call per month the DateRule spans (a
    genuinely month-scoped fallback) -- and keep only the fares that
    satisfy the DateRule. A real provider never lets us ask "what does
    this exact date cost", so this checks each fare it actually offers
    against the rule rather than generating dates and pricing them.
    """
    cities = load_cities()
    date_rule = spec.date_rule
    months = departure_months(date_rule)

    candidates: list[Candidate] = []
    for city_id in city_ids:
        city = cities[city_id]
        for origin in spec.origins:
            for destination_iata in city.iata:
                seen_pairs: set[tuple[date, date]] = set()

                fares = list(flight_provider.fare_calendar(origin, destination_iata))
                for year, month in months:
                    monthly_fare = flight_provider.cheapest_fare_in_month(
                        origin, destination_iata, year, month
                    )
                    if monthly_fare is not None:
                        fares.append(monthly_fare)

                for fare in fares:
                    if not matches_date_rule(fare.depart_date, fare.return_date, date_rule):
                        continue
                    key = (fare.depart_date, fare.return_date)
                    if key in seen_pairs:
                        continue  # the calendar sweep and a monthly fallback found the same fare
                    seen_pairs.add(key)
                    candidates.append(
                        _candidate_from_fare(origin, city_id, destination_iata, fare)
                    )
    return candidates


def shortlist_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Stage 2 input selection: the `limit` candidates with the cheapest
    flight price, since that's the strongest available signal before any
    stay has been priced."""
    return sorted(candidates, key=lambda c: c.flight.price.minor_units)[:limit]


def price_stays_and_assemble(
    spec: SearchSpec,
    shortlisted: list[Candidate],
    stay_provider: StayProvider,
) -> SearchResults:
    """Stage 2 (fetch) + stage 3 (assemble): price stays for the shortlist,
    keep the cheapest one passing the stay filters per destination/date
    combination, and build a fully priced Package for it.

    A shortlisted candidate contributes no Package if either nothing at
    that destination/date range satisfies the stay filters, or the
    destination has no bundled daily-spend estimate (catalog gap) — both
    are silent skips here; the caller decides whether an empty result set
    warrants a warning.
    """
    spend = load_spend()
    results = SearchResults(spec=spec)

    for candidate in shortlisted:
        key: _StayKey = (
            candidate.destination_city_id,
            candidate.depart_date,
            candidate.return_date,
        )
        if key not in results.raw_stays:
            results.raw_stays[key] = stay_provider.search(
                candidate.destination_city_id,
                candidate.depart_date,
                candidate.return_date,
                adults=spec.party_size,
            )
        raw_stays = results.raw_stays[key]
        chosen_stay = cheapest_passing(raw_stays, spec.stay_filters)
        if chosen_stay is None:
            continue

        daily_spend = spend.get(candidate.destination_city_id)
        if daily_spend is None:
            continue

        package = build_package(
            spec=spec,
            origin=candidate.origin,
            destination_city_id=candidate.destination_city_id,
            depart_date=candidate.depart_date,
            return_date=candidate.return_date,
            flight_price_per_person=candidate.flight.price,
            stay=chosen_stay,
            daily_spend=daily_spend,
            flight_deep_link=candidate.flight.deep_link,
        )
        results.packages.append(package)

    return results


def run_search(
    spec: SearchSpec,
    flight_provider: FlightProvider,
    stay_provider: StayProvider,
    *,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
) -> SearchResults:
    """Run the full three-stage funnel for one SearchSpec."""
    city_ids = resolve_destination(spec.destination)
    candidates = sweep_flights(spec, city_ids, flight_provider)
    shortlisted = shortlist_candidates(candidates, shortlist_size)
    results = price_stays_and_assemble(spec, shortlisted, stay_provider)
    results.flight_requests_made = getattr(flight_provider, "request_count", 0)
    return results
