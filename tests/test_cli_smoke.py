"""Phase 0 smoke test: the CLI installs and its commands dispatch correctly.

Real behavioural tests for each command land alongside the phase that
implements it (see the project plan).
"""

from typer.testing import CliRunner

from holiday_tracker.cli import app

runner = CliRunner()


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help exits 2 (click convention for "help shown, no command given")
    assert result.exit_code == 2
    assert "search" in result.output


def test_init_command_dispatches() -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_watch_subcommand_dispatches() -> None:
    result = runner.invoke(app, ["watch", "list"])
    assert result.exit_code == 0
    assert "watch list" in result.output


def test_dates_debug_command() -> None:
    result = runner.invoke(
        app,
        [
            "dates",
            "--window-start",
            "2027-03-04",
            "--window-end",
            "2027-03-04",
            "--depart-dow",
            "thu",
            "--nights-min",
            "3",
            "--nights-max",
            "4",
        ],
    )
    assert result.exit_code == 0
    assert "2027-03-04 -> 2027-03-07" in result.output
    assert "2 candidate date pair(s)." in result.output


def test_dates_debug_command_reports_no_matches() -> None:
    result = runner.invoke(
        app,
        [
            "dates",
            "--window-start",
            "2027-06-02",
            "--window-end",
            "2027-06-02",
            "--depart-dow",
            "thu",
        ],
    )
    assert result.exit_code == 0
    assert "No dates satisfy this rule." in result.output
