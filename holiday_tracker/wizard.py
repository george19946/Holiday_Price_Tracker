"""The interactive "quick list" that `holiday-track search` runs when
invoked with no flags -- asks for exactly the inputs the non-interactive
flag surface covers, in the order the project plan specifies: where you'll
fly from, where you want to go, dates (window, weekdays, nights,
blackouts), party size, budget, spend style, and finally the four stay
filters as a quick yes/no list.

Every prompt call is passed `console=console` explicitly rather than
relying on rich's default global console: that's what lets a caller (the
CLI, or a test) inject one Console for both the informational messages and
the actual prompts, instead of prompts silently reading from real stdin
regardless of what's passed to run_wizard().
"""

from __future__ import annotations

from datetime import date

from rich.console import Console
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt

from holiday_tracker.catalog.loader import resolve_destination
from holiday_tracker.models import DateRule, Money, SearchSpec, SpendStyle, StayFilters, Weekday


def _prompt_origins(console: Console) -> list[str]:
    while True:
        raw = Prompt.ask(
            "Which airport(s) are you willing to fly from? (comma-separated codes)",
            default="LHR",
            console=console,
        )
        codes = [code.strip().upper() for code in raw.split(",") if code.strip()]
        if codes:
            return codes
        console.print("[red]Enter at least one airport code.[/]")


def _prompt_destination(console: Console) -> str:
    while True:
        raw = Prompt.ask(
            'Where do you want to go? (a city, e.g. "Barcelona", or a region, '
            'e.g. "Western Europe")',
            console=console,
        )
        try:
            resolve_destination(raw)
        except ValueError:
            console.print(f"[red]Unrecognised destination {raw!r}. Try a city or region name.[/]")
            continue
        return raw


def _prompt_window(console: Console) -> tuple[date, date]:
    while True:
        start_raw = Prompt.ask("Earliest date you could travel (YYYY-MM-DD)", console=console)
        end_raw = Prompt.ask("Latest date you could travel (YYYY-MM-DD)", console=console)
        try:
            start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        except ValueError:
            console.print("[red]Enter dates as YYYY-MM-DD.[/]")
            continue
        if start > end:
            console.print("[red]The earliest date must be on or before the latest date.[/]")
            continue
        return start, end


def _prompt_weekdays(console: Console, question: str) -> set[Weekday]:
    raw = Prompt.ask(
        f"{question} (comma-separated day codes, e.g. thu,fri, or 'any')",
        default="any",
        console=console,
    )
    if raw.strip().lower() in ("", "any"):
        return set()
    try:
        return {Weekday(day.strip().lower()) for day in raw.split(",") if day.strip()}
    except ValueError:
        console.print("[red]Unrecognised day code -- treating as 'any'.[/]")
        return set()


def _prompt_nights(console: Console) -> tuple[int, int]:
    low = IntPrompt.ask("Minimum nights", default=3, console=console)
    high = IntPrompt.ask("Maximum nights", default=low, console=console)
    return min(low, high), max(low, high)


def _prompt_blackouts(console: Console) -> list[tuple[date, date]]:
    console.print(
        "Any dates you must avoid (e.g. a work commitment, Christmas)? "
        "Enter one at a time as YYYY-MM-DD or START:END; leave blank when done."
    )
    blackouts: list[tuple[date, date]] = []
    while True:
        raw = Prompt.ask("Blackout date/range", default="", console=console)
        if not raw.strip():
            return blackouts
        try:
            if ":" in raw:
                start_str, end_str = raw.split(":", 1)
                blackouts.append(
                    (date.fromisoformat(start_str.strip()), date.fromisoformat(end_str.strip()))
                )
            else:
                d = date.fromisoformat(raw.strip())
                blackouts.append((d, d))
        except ValueError:
            console.print("[red]Couldn't parse that -- use YYYY-MM-DD or YYYY-MM-DD:YYYY-MM-DD.[/]")


def _prompt_spend_style(console: Console) -> SpendStyle:
    raw = Prompt.ask(
        "Spending style",
        choices=["thrifty", "normal", "comfortable"],
        default="normal",
        console=console,
    )
    return SpendStyle(raw)


def _prompt_stay_filters(console: Console) -> StayFilters:
    console.print("\nA few quick questions about where you'll stay:")
    exclude_hostels = Confirm.ask(
        "Rule out hostels / shared rooms?", default=False, console=console
    )
    min_rating = (
        FloatPrompt.ask("Minimum guest rating (0-10)", default=7.5, console=console)
        if Confirm.ask("Set a minimum guest rating?", default=False, console=console)
        else None
    )
    max_centre_km = (
        FloatPrompt.ask("Maximum distance from the centre (km)", default=3.0, console=console)
        if Confirm.ask("Set a maximum distance from the centre?", default=False, console=console)
        else None
    )
    free_cancellation_only = Confirm.ask(
        "Only consider free-cancellation rates?", default=False, console=console
    )
    return StayFilters(
        exclude_hostels=exclude_hostels,
        min_rating=min_rating,
        max_centre_km=max_centre_km,
        free_cancellation_only=free_cancellation_only,
    )


def run_wizard(console: Console | None = None) -> SearchSpec:
    """Interactively collect every input `search` needs and return a
    validated SearchSpec."""
    console = console or Console()

    origins = _prompt_origins(console)
    destination = _prompt_destination(console)
    window_start, window_end = _prompt_window(console)
    depart_dow = _prompt_weekdays(console, "Depart on which day(s)?")
    return_dow = _prompt_weekdays(console, "Return on which day(s)?")
    nights_min, nights_max = _prompt_nights(console)
    blackouts = _prompt_blackouts(console)
    party_size = IntPrompt.ask("How many people?", default=1, console=console)
    budget_amount = FloatPrompt.ask("Total budget for the whole trip", console=console)
    currency = Prompt.ask("Currency", default="GBP", console=console).upper()
    spend_style = _prompt_spend_style(console)
    stay_filters = _prompt_stay_filters(console)

    date_rule = DateRule(
        window_start=window_start,
        window_end=window_end,
        depart_dow=depart_dow,
        return_dow=return_dow,
        nights_min=nights_min,
        nights_max=nights_max,
        blackouts=blackouts,
    )
    return SearchSpec(
        origins=origins,
        destination=destination,
        date_rule=date_rule,
        budget=Money.from_major(budget_amount, currency),
        party_size=party_size,
        spend_style=spend_style,
        stay_filters=stay_filters,
    )
