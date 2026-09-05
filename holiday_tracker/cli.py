"""Command-line entry point for holiday-track.

Commands not yet implemented are stubbed and filled in phase by phase (see
the project plan). Keeping all top-level commands registered from the
start avoids Typer's single-command collapsing behaviour, where a Typer
app with only one registered command invokes it directly and ignores the
subcommand name on the command line.

`search` runs the three-stage search engine (holiday_tracker.engine.search)
against either the offline fixtures provider (the default -- no token
needed) or the real Travelpayouts/Hotellook providers, either through the
interactive wizard (holiday_tracker.wizard, when run with no flags) or a
non-interactive flag surface (for scripts and the scheduled watch job).
Results are rendered by holiday_tracker.report as rich terminal tables,
with an optional self-contained HTML report alongside.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from holiday_tracker.alerts import rules as alert_rules
from holiday_tracker.alerts.email import SmtpConfig, send_alert_email
from holiday_tracker.catalog.loader import resolve_destination
from holiday_tracker.dates import expand_date_pairs
from holiday_tracker.engine.search import (
    DEFAULT_SHORTLIST_SIZE,
    estimate_flight_requests,
    run_search,
)
from holiday_tracker.models import (
    DateRule,
    Money,
    SearchSpec,
    SpendStyle,
    StayFilters,
    Weekday,
)
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider
from holiday_tracker.providers.hotellook import HotellookStayProvider
from holiday_tracker.providers.http import CachedHttpClient, ResponseCache, TokenBucket
from holiday_tracker.providers.travelpayouts import TravelpayoutsFlightProvider
from holiday_tracker.report import city_label, print_results, write_html_report
from holiday_tracker.store import db, repo
from holiday_tracker.wizard import run_wizard

app = typer.Typer(
    name="holiday-track",
    help="Budget-first holiday price tracker.",
    no_args_is_help=True,
)

watch_app = typer.Typer(help="Manage persisted watches that re-price on a schedule.")
app.add_typer(watch_app, name="watch")

_TRAVELPAYOUTS_HOST = "api.travelpayouts.com"
_HOTELLOOK_HOST = "engine.hotellook.com"


def _state_home() -> Path:
    """Base directory for local, per-machine state (HTTP cache, last search).

    Resolved at call time (not import time) from HOLIDAY_TRACKER_HOME so
    tests -- and users who want state somewhere other than the default --
    can redirect it; see conftest.py's autouse fixture, which sets this for
    every test to guarantee nothing here ever touches a real home directory.
    """
    override = os.environ.get("HOLIDAY_TRACKER_HOME")
    return Path(override) if override else Path.home() / ".holiday-tracker"


def _cache_path() -> Path:
    return _state_home() / "http_cache.sqlite"


def _last_search_path() -> Path:
    return _state_home() / "last_search.json"


def _default_history_dir() -> Path:
    # Resolved at call time (not baked in as a Typer option default at
    # decoration time) so $HOLIDAY_TRACKER_HISTORY_DIR -- including the
    # per-test override in conftest.py -- actually takes effect.
    return Path(os.environ.get("HOLIDAY_TRACKER_HISTORY_DIR", "data/history"))


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
        cache_path = _cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache = ResponseCache(cache_path)
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


def _spec_from_flags(
    *,
    from_: str,
    to: str,
    window: str,
    depart_dow: list[str],
    return_dow: list[str],
    nights: str,
    blackout: list[str],
    month: list[int],
    budget: float,
    currency: str,
    party: int,
    occupancy: int,
    style: str,
    no_hostels: bool,
    min_rating: float | None,
    max_centre_km: float | None,
    free_cancellation: bool,
) -> SearchSpec:
    window_start, window_end = _parse_window(window)
    nights_min, nights_max = _parse_nights(nights)
    date_rule = DateRule(
        window_start=window_start,
        window_end=window_end,
        depart_dow={Weekday(d) for d in depart_dow},
        return_dow={Weekday(d) for d in return_dow},
        nights_min=nights_min,
        nights_max=nights_max,
        blackouts=[_parse_blackout(b) for b in blackout],
        months=set(month) if month else None,
    )
    stay_filters = StayFilters(
        exclude_hostels=no_hostels,
        min_rating=min_rating,
        max_centre_km=max_centre_km,
        free_cancellation_only=free_cancellation,
    )
    return SearchSpec(
        origins=_parse_origins(from_),
        destination=to,
        date_rule=date_rule,
        budget=Money.from_major(budget, currency.upper()),
        party_size=party,
        occupancy_per_room=occupancy,
        spend_style=SpendStyle(style),
        stay_filters=stay_filters,
    )


def _require_flags(
    *, to: str | None, window: str | None, nights: str | None, budget: float | None
) -> None:
    """Shared validation for the non-interactive flag surface of both
    `search` and `watch add`: --from alone isn't enough to skip the wizard,
    the rest of the core fields must come along with it."""
    missing = [
        flag
        for flag, value in [("--to", to), ("--window", window), ("--nights", nights), ("--budget", budget)]
        if value is None
    ]
    if missing:
        typer.echo(
            f"missing required option(s): {', '.join(missing)} "
            "(or omit --from entirely for the interactive wizard)",
            err=True,
        )
        raise typer.Exit(code=1)


def _save_last_search(spec: SearchSpec) -> None:
    """Remember the most recent search so `watch add --from-last` can turn
    it into a watch without retyping every flag."""
    path = _last_search_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.model_dump_json(), encoding="utf-8")


def _load_last_search() -> SearchSpec:
    path = _last_search_path()
    if not path.exists():
        typer.echo(
            f"no previous search found at {path} -- run `holiday-track search` "
            "first, or pass search flags directly to `watch add`.",
            err=True,
        )
        raise typer.Exit(code=1)
    return SearchSpec.model_validate_json(path.read_text(encoding="utf-8"))


def _default_watch_name(spec: SearchSpec) -> str:
    return f"{spec.destination.strip().lower().replace(' ', '_')}-{spec.date_rule.window_start.isoformat()}"


def _run_and_report(
    spec: SearchSpec,
    *,
    provider: str,
    shortlist_size: int,
    near_miss_count: int,
    html_report: Path | None,
) -> None:
    # A fixed, generous width rather than terminal auto-detection: this
    # output is as likely to end up in a redirected log (the scheduled
    # GitHub Action, a piped report) as in an interactive terminal, and a
    # narrow auto-detected width (80 columns in most non-tty contexts)
    # truncates destination names and dates into unreadable ellipses.
    console = Console(width=120)

    try:
        city_ids = resolve_destination(spec.destination)
    except ValueError as exc:
        typer.echo(f"invalid search: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _save_last_search(spec)

    date_pair_count = len(expand_date_pairs(spec.date_rule))
    estimated_requests = estimate_flight_requests(spec, city_ids)
    console.print(
        f"Scanning {len(city_ids)} destination(s) x {date_pair_count} date pair(s) "
        f"(~{estimated_requests} flight-calendar request(s))..."
    )

    flight_provider, stay_provider = _build_providers(provider, spec.budget.currency)
    results = run_search(spec, flight_provider, stay_provider, shortlist_size=shortlist_size)

    print_results(console, spec, results, near_miss_count=near_miss_count)

    if html_report is not None:
        write_html_report(html_report, spec, results, near_miss_count=near_miss_count)
        console.print(f"\nHTML report written to {html_report}")


@app.command()
def search(
    from_: str | None = typer.Option(
        None, "--from", help="Comma-separated origin airport codes, e.g. LHR,LGW"
    ),
    to: str | None = typer.Option(
        None, "--to", help='Destination city or region, e.g. "Barcelona" or "Western Europe"'
    ),
    window: str | None = typer.Option(
        None, "--window", help="START:END, e.g. 2027-01-01:2027-12-31"
    ),
    depart_dow: list[str] = typer.Option(
        [], "--depart-dow", help="e.g. thu (repeatable); omit for any day"
    ),
    return_dow: list[str] = typer.Option(
        [], "--return-dow", help="e.g. sun (repeatable); omit for any day"
    ),
    nights: str | None = typer.Option(None, "--nights", help='e.g. "3" or "3-4"'),
    blackout: list[str] = typer.Option(
        [], "--blackout", help="a date or START:END range to avoid; repeatable"
    ),
    month: list[int] = typer.Option(
        [], "--month", help="restrict departure to month(s) 1-12; repeatable"
    ),
    budget: float | None = typer.Option(None, "--budget"),
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
    html_report: Path | None = typer.Option(
        None, "--html-report", help="Also write a self-contained HTML report to this path"
    ),
) -> None:
    """Search for a holiday that fits the given constraints.

    Run with no flags for an interactive quick-list wizard that asks for
    every input one at a time. Pass --from (and the other flags) for a
    non-interactive run suitable for scripts or the scheduled watch job.

    Either way, prints the cheapest package that fits the budget and every
    stay filter, or -- if none does -- the cheapest near-misses, each
    annotated with the single cheapest change that would close the gap.
    Defaults to the offline fixtures provider so this works with no API
    token and no network access; pass --provider travelpayouts for real
    (but cached, indicative -- see the README) prices.
    """
    if from_ is None:
        spec = run_wizard()
    else:
        _require_flags(to=to, window=window, nights=nights, budget=budget)
        try:
            spec = _spec_from_flags(
                from_=from_,
                to=to,
                window=window,
                depart_dow=depart_dow,
                return_dow=return_dow,
                nights=nights,
                blackout=blackout,
                month=month,
                budget=budget,
                currency=currency,
                party=party,
                occupancy=occupancy,
                style=style,
                no_hostels=no_hostels,
                min_rating=min_rating,
                max_centre_km=max_centre_km,
                free_cancellation=free_cancellation,
            )
        except (ValidationError, ValueError) as exc:
            typer.echo(f"invalid search: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    _run_and_report(
        spec,
        provider=provider,
        shortlist_size=shortlist_size,
        near_miss_count=near_miss_count,
        html_report=html_report,
    )


@app.command()
def report(
    watch_id: str = typer.Argument(..., help="Watch id to report on."),
    db_path: str | None = typer.Option(None, "--db-path", hidden=True),
) -> None:
    """Show price history and trend for a tracked watch."""
    conn = db.get_connection(db_path)
    watch = repo.get_watch(conn, watch_id)
    if watch is None:
        typer.echo(f"No such watch: {watch_id}", err=True)
        raise typer.Exit(code=1)

    history = repo.run_history(conn, watch_id)
    console = Console(width=120)
    console.print(
        f"[bold]{watch.name}[/] ({watch.id}) -- {watch.spec.destination}, "
        f"budget {watch.spec.budget}, status {watch.status}"
    )
    if not history:
        console.print(f"No recorded runs yet. Run `holiday-track watch run {watch.id}` to price it.")
        return

    table = Table(title="Run history")
    table.add_column("Run at")
    table.add_column("Best destination")
    table.add_column("Best total", justify="right")
    table.add_column("Fits budget?")
    table.add_column("Candidates", justify="right")
    for run in history:
        best = run.best_package
        table.add_row(
            run.started_at.strftime("%Y-%m-%d %H:%M"),
            city_label(best.destination_city_id) if best else ("error" if run.error else "-"),
            str(best.total_cost) if best else "-",
            ("yes" if best.fits_budget else "no") if best else "-",
            str(run.candidates_scanned),
        )
    console.print(table)


@watch_app.command("add")
def watch_add(
    from_last: bool = typer.Option(
        False, "--from-last", help="Reuse the constraints from the last `search` run"
    ),
    name: str | None = typer.Option(None, "--name", help="A short name for this watch"),
    from_: str | None = typer.Option(
        None, "--from", help="Comma-separated origin airport codes, e.g. LHR,LGW"
    ),
    to: str | None = typer.Option(None, "--to"),
    window: str | None = typer.Option(None, "--window"),
    depart_dow: list[str] = typer.Option([], "--depart-dow"),
    return_dow: list[str] = typer.Option([], "--return-dow"),
    nights: str | None = typer.Option(None, "--nights"),
    blackout: list[str] = typer.Option([], "--blackout"),
    month: list[int] = typer.Option([], "--month"),
    budget: float | None = typer.Option(None, "--budget"),
    currency: str = typer.Option("GBP", "--currency"),
    party: int = typer.Option(1, "--party"),
    occupancy: int = typer.Option(2, "--occupancy"),
    style: str = typer.Option("normal", "--style"),
    no_hostels: bool = typer.Option(False, "--no-hostels"),
    min_rating: float | None = typer.Option(None, "--min-rating"),
    max_centre_km: float | None = typer.Option(None, "--max-centre-km"),
    free_cancellation: bool = typer.Option(False, "--free-cancellation"),
    db_path: str | None = typer.Option(None, "--db-path", hidden=True),
) -> None:
    """Persist a search's constraints as a watch, so `holiday-track watch
    run` can re-price it on a schedule and alert you once it first fits
    budget. Pass --from-last to reuse your last `search`'s constraints,
    the same flags `search` accepts to build a fresh one, or neither for
    the interactive wizard.
    """
    if from_last:
        spec = _load_last_search()
    elif from_ is not None:
        _require_flags(to=to, window=window, nights=nights, budget=budget)
        try:
            spec = _spec_from_flags(
                from_=from_,
                to=to,
                window=window,
                depart_dow=depart_dow,
                return_dow=return_dow,
                nights=nights,
                blackout=blackout,
                month=month,
                budget=budget,
                currency=currency,
                party=party,
                occupancy=occupancy,
                style=style,
                no_hostels=no_hostels,
                min_rating=min_rating,
                max_centre_km=max_centre_km,
                free_cancellation=free_cancellation,
            )
        except (ValidationError, ValueError) as exc:
            typer.echo(f"invalid search: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    else:
        spec = run_wizard()

    conn = db.get_connection(db_path)
    watch = repo.create_watch(conn, spec, name or _default_watch_name(spec))
    typer.echo(
        f"Created watch {watch.id} ({watch.name}) -- {spec.destination}, budget {spec.budget}.\n"
        f"Run it with `holiday-track watch run {watch.id}`."
    )


@watch_app.command("list")
def watch_list(db_path: str | None = typer.Option(None, "--db-path", hidden=True)) -> None:
    """List all watches, with the best price known from their last run."""
    conn = db.get_connection(db_path)
    watches = repo.list_watches(conn)
    if not watches:
        typer.echo("No watches yet. Create one with `holiday-track watch add`.")
        return

    console = Console(width=120)
    table = Table(title="Watches")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Destination")
    table.add_column("Budget", justify="right")
    table.add_column("Status")
    table.add_column("Last run")
    table.add_column("Best so far", justify="right")

    for watch in watches:
        latest = repo.latest_run(conn, watch.id)
        best = latest.best_package if latest else None
        best_cell = f"{best.total_cost} ({'fits' if best.fits_budget else 'over'})" if best else "-"
        table.add_row(
            watch.id,
            watch.name,
            watch.spec.destination,
            str(watch.spec.budget),
            watch.status,
            watch.last_run_at.strftime("%Y-%m-%d %H:%M") if watch.last_run_at else "never",
            best_cell,
        )
    console.print(table)


@watch_app.command("rm")
def watch_rm(
    watch_id: str = typer.Argument(..., help="Watch id to remove."),
    db_path: str | None = typer.Option(None, "--db-path", hidden=True),
) -> None:
    """Remove a watch. Its committed price history (data/history/<id>.jsonl),
    if any, is left in place -- it's a historical record, not watch state."""
    conn = db.get_connection(db_path)
    if repo.delete_watch(conn, watch_id):
        typer.echo(f"Removed watch {watch_id}.")
    else:
        typer.echo(f"No such watch: {watch_id}", err=True)
        raise typer.Exit(code=1)


@watch_app.command("run")
def watch_run(
    watch_id: str | None = typer.Argument(
        None, help="Only re-price this watch; omit to run every active watch."
    ),
    provider: str = typer.Option("fixtures", "--provider", help="fixtures | travelpayouts"),
    shortlist_size: int = typer.Option(DEFAULT_SHORTLIST_SIZE, "--shortlist-size"),
    history_dir: Path | None = typer.Option(
        None,
        "--history-dir",
        help="Where to append data/history/<watch>.jsonl summaries "
        "(default: data/history, or $HOLIDAY_TRACKER_HISTORY_DIR)",
    ),
    no_alerts: bool = typer.Option(
        False, "--no-alerts", help="Skip sending alert emails even if a package now fits budget"
    ),
    db_path: str | None = typer.Option(None, "--db-path", hidden=True),
) -> None:
    """Re-price one watch, or every active watch if none is given.

    This is what the scheduled GitHub Action (phase 7) runs daily. Each
    run is recorded in the local database and summarised as one line
    appended to data/history/<watch>.jsonl. A provider failure on one
    watch is recorded as that watch's run error rather than aborting the
    rest of the batch. When a watch's best package first fits its budget,
    an alert email is sent if SMTP_HOST/SMTP_USER/SMTP_PASS/ALERT_TO are
    configured (see the README) -- otherwise that's reported, not treated
    as an error, since alerting is optional.
    """
    resolved_history_dir = history_dir if history_dir is not None else _default_history_dir()

    conn = db.get_connection(db_path)
    if watch_id is not None:
        watch = repo.get_watch(conn, watch_id)
        if watch is None:
            typer.echo(f"No such watch: {watch_id}", err=True)
            raise typer.Exit(code=1)
        watches = [watch]
    else:
        watches = [w for w in repo.list_watches(conn) if w.status == "active"]

    if not watches:
        typer.echo("No active watches to run.")
        return

    console = Console(width=120)
    for watch in watches:
        started_at = datetime.now()
        flight_provider, stay_provider = _build_providers(provider, watch.spec.budget.currency)
        results = None
        error = None
        try:
            results = run_search(
                watch.spec, flight_provider, stay_provider, shortlist_size=shortlist_size
            )
        except Exception as exc:  # a provider outage must not abort the rest of the batch
            error = str(exc)
        finished_at = datetime.now()

        run = repo.record_run(
            conn,
            watch.id,
            started_at=started_at,
            finished_at=finished_at,
            requests_used=getattr(flight_provider, "request_count", 0),
            results=results,
            error=error,
        )
        repo.append_price_history(resolved_history_dir, watch.id, run)

        if error:
            console.print(f"[red]{watch.name} ({watch.id}): failed -- {error}[/]")
        elif run.best_package is not None:
            status = "fits budget" if run.best_package.fits_budget else "over budget"
            console.print(f"{watch.name} ({watch.id}): best {run.best_package.total_cost} ({status})")
        else:
            console.print(f"{watch.name} ({watch.id}): no candidates matched")

        if error or no_alerts:
            continue

        decision = alert_rules.decide(conn, watch.id, run.best_package)
        if not decision.should_send:
            continue

        smtp_config = SmtpConfig.from_env()
        if smtp_config is None:
            console.print(
                "  [yellow]Package fits budget, but no alert sent -- set SMTP_HOST/SMTP_USER/"
                "SMTP_PASS/ALERT_TO to enable email alerts.[/]"
            )
            continue

        try:
            send_alert_email(smtp_config, watch.name, decision.package)
        except OSError as exc:
            console.print(f"  [red]Failed to send alert email: {exc}[/]")
        else:
            alert_rules.record_sent(conn, watch.id, decision.fingerprint)
            console.print(f"  [green]Alert email sent to {smtp_config.recipient}.[/]")


if __name__ == "__main__":
    app()
