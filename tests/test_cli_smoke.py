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


class TestSearchCommand:
    def _run(self, *extra_args: str):
        base = [
            "search",
            "--provider",
            "fixtures",
            "--from",
            "LHR",
            "--to",
            "barcelona",
            "--window",
            "2027-03-01:2027-05-31",
            "--depart-dow",
            "thu",
            "--return-dow",
            "sun",
            "--nights",
            "3",
            "--party",
            "2",
        ]
        return runner.invoke(app, [*base, *extra_args])

    def test_feasible_result_is_reported(self):
        result = self._run("--budget", "2000")
        assert result.exit_code == 0
        assert "Found a holiday that fits" in result.output
        assert "Barcelona" in result.output

    def test_near_miss_result_is_reported_with_relaxation(self):
        result = self._run("--budget", "1")
        assert result.exit_code == 0
        assert "Nothing fits" in result.output
        assert "Barcelona" in result.output

    def test_unknown_destination_exits_nonzero_with_message(self):
        result = self._run("--budget", "500", "--to", "Atlantis")
        # --to appears twice on the command line; typer/click takes the last value
        assert result.exit_code == 1
        assert "unknown destination" in result.output

    def test_invalid_window_format_is_rejected(self):
        result = runner.invoke(
            app,
            [
                "search",
                "--provider",
                "fixtures",
                "--from",
                "LHR",
                "--to",
                "barcelona",
                "--window",
                "not-a-window",
                "--nights",
                "3",
                "--budget",
                "500",
            ],
        )
        assert result.exit_code != 0

    def test_missing_token_for_travelpayouts_provider_fails_cleanly(self, monkeypatch):
        monkeypatch.delenv("TRAVELPAYOUTS_TOKEN", raising=False)
        result = self._run("--budget", "500", "--provider", "travelpayouts")
        assert result.exit_code == 1
        assert "TRAVELPAYOUTS_TOKEN is not set" in result.output

    def test_stay_filters_are_applied(self):
        result = self._run("--budget", "2000", "--no-hostels", "--min-rating", "9.99")
        # fixtures cap ratings at 9.9, so a 9.99 minimum eliminates every stay
        assert result.exit_code == 0
        assert "No results at all" in result.output

    def test_invalid_nights_format_is_rejected(self):
        result = runner.invoke(
            app,
            [
                "search", "--provider", "fixtures", "--from", "LHR", "--to", "barcelona",
                "--window", "2027-01-01:2027-02-01", "--nights", "not-a-number", "--budget", "500",
            ],
        )
        assert result.exit_code != 0

    def test_invalid_blackout_format_is_rejected(self):
        result = runner.invoke(
            app,
            [
                "search", "--provider", "fixtures", "--from", "LHR", "--to", "barcelona",
                "--window", "2027-01-01:2027-02-01", "--nights", "3", "--budget", "500",
                "--blackout", "not-a-date",
            ],
        )
        assert result.exit_code != 0

    def test_blackout_and_month_restriction_are_accepted(self):
        result = self._run(
            "--budget", "2000",
            "--blackout", "2027-04-01:2027-04-30",
            "--month", "3",
        )
        assert result.exit_code == 0
        assert "Found a holiday that fits" in result.output

    def test_comma_separated_origins_are_accepted(self):
        result = runner.invoke(
            app,
            [
                "search", "--provider", "fixtures", "--from", "LHR,MAN", "--to", "barcelona",
                "--window", "2027-03-01:2027-05-31", "--nights", "3", "--budget", "2000",
            ],
        )
        assert result.exit_code == 0
