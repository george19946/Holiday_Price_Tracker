"""Command-line entry point for holiday-track.

Commands not yet implemented are stubbed and filled in phase by phase (see
the project plan). Keeping all top-level commands registered from the
start avoids Typer's single-command collapsing behaviour, where a Typer
app with only one registered command invokes it directly and ignores the
subcommand name on the command line.

`search` is the one fully wired up so far: a non-interactive, flag-driven
run of the three-stage search engine (holiday_tracker.engine.search)
against either the offline fixtures provider (the default -- no token
needed) or the real Travelpayouts/Hotellook providers. The interactive
wizard that asks for these same inputs step by step, plus a properly
formatted (rich/HTML) report, are phase 4 work; this command's plain-text
output is what phase 4 replaces.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import typer
from pydantic import ValidationError

from holiday_tracker.catalog.loader import resolve_destination
from holiday_tracker.dates import expand_date_pairs
from holiday_tracker.engine.rank import describe_relaxation, suggest_relaxation
from holiday_tracker.engine.search import (
    DEFAULT_SHORTLIST_SIZE,
    estimate_flight_requests,
    run_search,
)
from holiday_tracker.models import (
    DateRule,
    Money,
    Package,
    SearchSpec,
    SpendStyle,
    StayFilters,
    Weekday,
)
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider
from holiday_tracker.providers.hotellook import HotellookStayProvider
from holiday_tracker.providers.http import CachedHttpClient, ResponseCache, TokenBucket
from holiday_tracker.providers.travelpayouts import TravelpayoutsFlightProvider

app = typer.Typer(
    name="holiday-track",
    help="Budget-first holiday price tracker.",
    no_args_is_help=True,
)

watch_app = typer.Typer(help="Manage persisted watches that re-price on a schedule.")
app.add_typer(watch_app, name="watch")

_DEFAULT_CACHE_PATH = Path.home() / ".holiday-tracker" / "http_cache.sqlite"
_TRAVELPAYOUTS_HOST = "api.travelpayouts.com"
_HOTELLOOK_HOST = "engine.hotellook.com"


@app.command()
def init() -> None:
    """Write local config, fetch static catalogues, and check for an API token."""
    typer.echo("holiday-track init: not yet implemented (see project plan, phase 1-2).")


@app.command()
def dates(
    window_start: str = typer.Option(..., "--window-start", help="YYYY-MM-DD"),
    window_end: str = typer.Option(..., "--window-end", help="YYYY-MM-DD"),
    depart_dow: list[str] = typer.Option(
        [], "--depart-dow", help="e.g. thu (repeatable); omit for any day"
    ),
    return_dow: list[str] = typer.Option(
        [], "--return-dow", help="e.g. sun (repeatable); omit for any day"
    ),
    nights_min: int = typer.Option(1, "--nights-min"),
    nights_max: int | None = typer.Option(None, "--nights-max", help="defaults to nights-min"),
) -> None:
    """Debug helper: expand a date rule into concrete (depart, return) pairs
    without spending any API requests on it."""
    rule = DateRule(
        window_start=date.fromisoformat(window_start),
        window_end=date.fromisoformat(window_end),
        depart_dow={Weekday(d) for d in depart_dow},
        return_dow={Weekday(d) for d in return_dow},
        nights_min=nights_min,
        nights_max=nights_max if nights_max is not None else nights_min,
    )
    pairs = expand_date_pairs(rule)
    if not pairs:
        typer.echo("No dates satisfy this rule.")
        raise typer.Exit(code=0)
    for depart, return_ in pairs:
        nights = (return_ - depart).days
        typer.echo(f"{depart.isoformat()} -> {return_.isoformat()}  ({nights} nights)")
    typer.echo(f"\n{len(pairs)} candidate date pair(s).")


# --------------------------------------------------------------------------
# search: flag parsing helpers
# --------------------------------------------------------------------------


def _parse_origins(value: str) -> list[str]:
    codes = [code.strip() for code in value.split(",") if code.strip()]
    if not codes:
        raise typer.BadParameter("expected at least one airport code, e.g. LHR or LHR,LGW")
    return codes


def _parse_window(value: str) -> tuple[date, date]:
    try:
        start_str, end_str = value.split(":", 1)
        return date.fromisoformat(start_str), date.fromisoformat(end_str)
    except ValueError as exc:
        raise typer.BadParameter("expected START:END, e.g. 2027-01-01:2027-12-31") from exc


def _parse_nights(value: str) -> tuple[int, int]:
    try:
        if "-" in value:
            low, high = value.split("-", 1)
            return int(low), int(high)
        n = int(value)
        return n, n
    except ValueError as exc:
        raise typer.BadParameter('expected e.g. "3" or "3-4"') from exc


def _parse_blackout(value: str) -> tuple[date, date]:
    try:
        if ":" in value:
            start_str, end_str = value.split(":", 1)
            return date.fromisoformat(start_str), date.fromisoformat(end_str)
        d = date.fromisoformat(value)
        return d, d
    except ValueError as exc:
        raise typer.BadParameter(
            "expected a date (YYYY-MM-DD) or a range (START:END)"
        ) from exc


def _city_label(city_id: str) -> str:
    return city_id.replace("_", " ").title()


def _print_package(package: Package) -> None:
    typer.echo(
        f"{_city_label(package.destination_city_id)} ({package.origin} -> "
        f"{package.depart_date.isoformat()}..{package.return_date.isoformat()}, "
        f"{package.nights} nights): {package.total_cost} total"
    )
    typer.echo(
        f"    flights {package.flights_cost}  +  stay {package.accommodation_cost}  +  "
        f"spend {package.spend_cost}"
    )
    if package.stay is not None:
        stay = package.stay
        typer.echo(
            f"    stay: {stay.property_type}, rating {stay.rating}, "
            f"{stay.distance_km} km from centre (observed {stay.observed_at:%Y-%m-%d}, "
            f"source: {stay.source} -- indicative, verify before booking)"
        )


def _build_providers(provider: str, currency: str) -> tuple[object, object]:
    if provider == "fixtures":
        return FixturesFlightProvider(), FixturesStayProvider()

    if provider == "travelpayouts":
        token = os.environ.get("TRAVELPAYOUTS_TOKEN")
        if not token:
            typer.echo(
                "TRAVELPAYOUTS_TOKEN is not set. Sign up free at https://www.travelpayouts.com/ "
                "and export it, or use --provider fixtures for an offline run.",
                err=True,
            )
            raise typer.Exit(code=1)
        _DEFAULT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache = ResponseCache(_DEFAULT_CACHE_PATH)
        limiters = {
            _TRAVELPAYOUTS_HOST: TokenBucket(rate_per_minute=300),
            _HOTELLOOK_HOST: TokenBucket(rate_per_minute=60),
        }
        http_client = CachedHttpClient(cache=cache, limiters=limiters)
        currency_code = currency.lower()
        return (
            TravelpayoutsFlightProvider(http_client, token=token, currency=currency_code),
            HotellookStayProvider(http_client, token=token, currency=currency_code),
        )

    typer.echo(f"unknown provider {provider!r}; expected 'fixtures' or 'travelpayouts'", err=True)
    raise typer.Exit(code=1)


@app.command()
def search(
    from_: str = typer.Option(
        ..., "--from", help="Comma-separated origin airport codes, e.g. LHR,LGW"
    ),
    to: str = typer.Option(
        ..., "--to", help='Destination city or region, e.g. "Barcelona" or "Western Europe"'
    ),
    window: str = typer.Option(..., "--window", help="START:END, e.g. 2027-01-01:2027-12-31"),
    depart_dow: list[str] = typer.Option(
        [], "--depart-dow", help="e.g. thu (repeatable); omit for any day"
    ),
    return_dow: list[str] = typer.Option(
        [], "--return-dow", help="e.g. sun (repeatable); omit for any day"
    ),
    nights: str = typer.Option(..., "--nights", help='e.g. "3" or "3-4"'),
    blackout: list[str] = typer.Option(
        [], "--blackout", help="a date or START:END range to avoid; repeatable"
    ),
    month: list[int] = typer.Option(
        [], "--month", help="restrict departure to month(s) 1-12; repeatable"
    ),
    budget: float = typer.Option(..., "--budget"),
    currency: str = typer.Option("GBP", "--currency"),
    party: int = typer.Option(1, "--party"),
    occupancy: int = typer.Option(2, "--occupancy", help="guests per room"),
    style: str = typer.Option("normal", "--style", help="thrifty | normal | comfortable"),
    no_hostels: bool = typer.Option(False, "--no-hostels"),
    min_rating: float | None = typer.Option(None, "--min-rating"),
    max_centre_km: float | None = typer.Option(None, "--max-centre-km"),
    free_cancellation: bool = typer.Option(False, "--free-cancellation"),
    provider: str = typer.Option("fixtures", "--provider", help="fixtures | travelpayouts"),
    shortlist_size: int = typer.Option(DEFAULT_SHORTLIST_SIZE, "--shortlist-size"),
    near_miss_count: int = typer.Option(5, "--near-miss-count"),
) -> None:
    """Search non-interactively for a holiday that fits the given constraints.

    Prints the cheapest package that fits the budget and every stay filter,
    or -- if none does -- the cheapest near-misses, each annotated with the
    single cheapest change that would close the gap. Defaults to the
    offline fixtures provider so this command works with no API token and
    no network access; pass --provider travelpayouts for real (but cached,
    indicative -- see the README) prices.
    """
    try:
        window_start, window_end = _parse_window(window)
        date_rule = DateRule(
            window_start=window_start,
            window_end=window_end,
            depart_dow={Weekday(d) for d in depart_dow},
            return_dow={Weekday(d) for d in return_dow},
            nights_min=_parse_nights(nights)[0],
            nights_max=_parse_nights(nights)[1],
            blackouts=[_parse_blackout(b) for b in blackout],
            months=set(month) if month else None,
        )
        stay_filters = StayFilters(
            exclude_hostels=no_hostels,
            min_rating=min_rating,
            max_centre_km=max_centre_km,
            free_cancellation_only=free_cancellation,
        )
        spec = SearchSpec(
            origins=_parse_origins(from_),
            destination=to,
            date_rule=date_rule,
            budget=Money.from_major(budget, currency.upper()),
            party_size=party,
            occupancy_per_room=occupancy,
            spend_style=SpendStyle(style),
            stay_filters=stay_filters,
        )
    except (ValidationError, ValueError) as exc:
        typer.echo(f"invalid search: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        city_ids = resolve_destination(spec.destination)
    except ValueError as exc:
        typer.echo(f"invalid search: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    date_pair_count = len(expand_date_pairs(date_rule))
    estimated_requests = estimate_flight_requests(spec, city_ids)
    typer.echo(
        f"Scanning {len(city_ids)} destination(s) x {date_pair_count} date pair(s) "
        f"(~{estimated_requests} flight-calendar request(s))..."
    )

    flight_provider, stay_provider = _build_providers(provider, currency)
    results = run_search(spec, flight_provider, stay_provider, shortlist_size=shortlist_size)

    if results.feasible:
        typer.echo(f"\nFound a holiday that fits {spec.budget}:\n")
        _print_package(results.feasible[0])
        return

    if results.near_misses:
        typer.echo(f"\nNothing fits {spec.budget} exactly. Cheapest alternative(s):\n")
        for package in results.near_misses[:near_miss_count]:
            _print_package(package)
            relaxation = suggest_relaxation(package, results.packages, spec, results.raw_stays)
            if relaxation is not None:
                typer.echo(f"    -> {describe_relaxation(package, relaxation)}")
            typer.echo("")
        return

    typer.echo(
        "\nNo results at all. Try loosening the stay filters, widening the date "
        "window, or checking a different destination."
    )


@app.command()
def report(watch_id: str = typer.Argument(..., help="Watch id to report on.")) -> None:
    """Show price history and trend for a tracked watch."""
    typer.echo(f"holiday-track report {watch_id}: not yet implemented (see project plan, phase 5).")


@watch_app.command("add")
def watch_add() -> None:
    """Persist a search's constraints as a watch."""
    typer.echo("holiday-track watch add: not yet implemented (see project plan, phase 5).")


@watch_app.command("list")
def watch_list() -> None:
    """List all watches."""
    typer.echo("holiday-track watch list: not yet implemented (see project plan, phase 5).")


@watch_app.command("rm")
def watch_rm(watch_id: str = typer.Argument(..., help="Watch id to remove.")) -> None:
    """Remove a watch."""
    typer.echo(f"holiday-track watch rm {watch_id}: not yet implemented (see project plan, phase 5).")


@watch_app.command("run")
def watch_run() -> None:
    """Re-price all active watches (used by the scheduled GitHub Action)."""
    typer.echo("holiday-track watch run: not yet implemented (see project plan, phase 5-6).")


if __name__ == "__main__":
    app()
