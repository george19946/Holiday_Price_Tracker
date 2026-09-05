"""Command-line entry point for holiday-track.

Commands are stubbed here in the initial scaffold and filled in phase by phase
(see the project plan). Keeping all top-level commands registered from the
start avoids Typer's single-command collapsing behaviour, where a Typer app
with only one registered command invokes it directly and ignores the
subcommand name on the command line.
"""

import typer

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
