"""Command-line entry point for holiday-track.

Commands are stubbed here in the initial scaffold and filled in phase by phase
(see the project plan). Keeping all top-level commands registered from the
start avoids Typer's single-command collapsing behaviour, where a Typer app
with only one registered command invokes it directly and ignores the
subcommand name on the command line.
"""

import typer

from holiday_tracker.dates import expand_date_pairs
from holiday_tracker.models import DateRule, Weekday

app = typer.Typer(
    name="holiday-track",
    help="Budget-first holiday price tracker.",
    no_args_is_help=True,
)

watch_app = typer.Typer(help="Manage persisted watches that re-price on a schedule.")
app.add_typer(watch_app, name="watch")


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
    from datetime import date as date_cls

    rule = DateRule(
        window_start=date_cls.fromisoformat(window_start),
        window_end=date_cls.fromisoformat(window_end),
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


@app.command()
def search() -> None:
    """Search interactively, or non-interactively with flags, for a holiday that fits."""
    typer.echo("holiday-track search: not yet implemented (see project plan, phase 3-4).")


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
