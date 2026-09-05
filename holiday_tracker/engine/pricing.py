"""Assembles the all-in cost of a holiday: flights + accommodation +
estimated daily spend, for the whole party, converted into the budget's
currency.

FX conversion here is deliberately simple and approximate -- there is no
free live FX-rate API bundled into this project, so FX_TO_GBP below is a
fixed, hand-maintained reference table (see the README's "honest
limitations"). It's good enough to compare a EUR-denominated daily-spend
estimate against a GBP budget within a few percent, not for financial
precision. Update the table by hand if a rate has drifted enough to matter.
"""

from __future__ import annotations

from datetime import date

from holiday_tracker.catalog.loader import DailySpend
from holiday_tracker.models import Money, Package, SearchSpec, SpendStyle, StayQuote

# Approximate, hand-maintained reference rates to GBP (as of the project's
# creation). Not live -- see module docstring.
FX_TO_GBP: dict[str, float] = {
    "GBP": 1.0,
    "EUR": 0.86,
    "USD": 0.79,
    "CHF": 0.90,
    "PLN": 0.20,
    "CZK": 0.034,
    "HUF": 0.0021,
    "DKK": 0.115,
    "SEK": 0.075,
    "NOK": 0.071,
    "ISK": 0.0057,
    "RON": 0.17,
    "BGN": 0.44,
    "RSD": 0.0073,
    "ALL": 0.0086,
    "MKD": 0.014,
}


def convert(money: Money, to_currency: str) -> Money:
    """Convert `money` into `to_currency` using the approximate FX_TO_GBP
    reference table. A no-op (returned unchanged) if already in that
    currency, so callers can convert defensively without a branch."""
    if money.currency == to_currency:
        return money
    if money.currency not in FX_TO_GBP:
        raise ValueError(f"no FX rate configured for {money.currency!r}")
    if to_currency not in FX_TO_GBP:
        raise ValueError(f"no FX rate configured for {to_currency!r}")
    gbp_amount = money.amount * FX_TO_GBP[money.currency]
    target_amount = gbp_amount / FX_TO_GBP[to_currency]
    return Money.from_major(target_amount, to_currency)


def spend_days_for(nights: int) -> int:
    """How many full days of spend a trip of `nights` nights is billed for.

    Modelling choice: arrival and departure are each treated as a half day
    (you arrive partway through day one and leave partway through the last
    day), which nets out to one spend-day per night -- a 3-night trip
    spans 4 calendar days but bills 3 spend-days. Kept as a named function,
    not inlined, so this choice has exactly one place to change and one
    place to explain in the rendered report (see report.py, phase 4).
    """
    return nights


def price_flights(per_person_fare: Money, party_size: int) -> Money:
    return per_person_fare * party_size


def price_accommodation(nightly_rate: Money, nights: int, rooms_needed: int) -> Money:
    return nightly_rate * nights * rooms_needed


def price_spend(
    daily_spend: DailySpend,
    style: SpendStyle,
    party_size: int,
    nights: int,
    currency: str,
) -> Money:
    per_person_per_day = daily_spend.daily_total_for_style(style)
    total_in_spend_currency = per_person_per_day * party_size * spend_days_for(nights)
    return convert(Money.from_major(total_in_spend_currency, daily_spend.currency), currency)


def build_package(
    *,
    spec: SearchSpec,
    origin: str,
    destination_city_id: str,
    depart_date: date,
    return_date: date,
    flight_price_per_person: Money,
    stay: StayQuote | None,
    daily_spend: DailySpend,
    flight_deep_link: str | None = None,
) -> Package:
    """Assemble one fully priced Package from its raw components, and
    determine whether it fits the search spec's budget."""
    nights = (return_date - depart_date).days
    currency = spec.budget.currency

    flights_cost = convert(price_flights(flight_price_per_person, spec.party_size), currency)

    if stay is not None:
        accommodation_cost = convert(
            price_accommodation(stay.nightly_rate, nights, spec.rooms_needed), currency
        )
    else:
        accommodation_cost = Money.zero(currency)

    spend_cost = price_spend(daily_spend, spec.spend_style, spec.party_size, nights, currency)

    total_cost = flights_cost + accommodation_cost + spend_cost
    fits = total_cost <= spec.budget
    over_budget_by = None if fits else (total_cost - spec.budget)

    return Package(
        destination_city_id=destination_city_id,
        origin=origin,
        depart_date=depart_date,
        return_date=return_date,
        nights=nights,
        party_size=spec.party_size,
        flights_cost=flights_cost,
        accommodation_cost=accommodation_cost,
        spend_cost=spend_cost,
        total_cost=total_cost,
        flight_deep_link=flight_deep_link,
        stay=stay,
        fits_budget=fits,
        over_budget_by=over_budget_by,
    )
