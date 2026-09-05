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
    assert "No watches yet" in result.output


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
        # A wide window and nights range, no weekday restriction: real
        # free-tier fare data is a sparse, opportunistic set (confirmed
        # against a live Travelpayouts token -- see
        # providers/travelpayouts.py), so a tightly constrained rule can
        # legitimately match nothing. These CLI-level tests want a
        # reliable "there are results" scenario; the sparse/narrow-rule
        # behavior itself is covered in tests/test_search_engine.py.
        base = [
            "search",
            "--provider",
            "fixtures",
            "--from",
            "LHR",
            "--to",
            "barcelona",
            "--window",
            "2027-01-01:2027-12-31",
            "--nights",
            "1-14",
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


class TestWatchCommands:
    def _spec_args(self, budget="2000"):
        """Flags accepted by `watch add` (no --provider -- that's a `watch run` concern)."""
        return [
            "--from", "LHR", "--to", "barcelona",
            "--window", "2027-03-01:2027-05-31", "--depart-dow", "thu", "--return-dow", "sun",
            "--nights", "3", "--party", "2", "--budget", budget,
        ]

    def _search_args(self, budget="2000"):
        return ["--provider", "fixtures", *self._spec_args(budget)]

    def test_watch_add_with_flags_then_list_then_run_then_report(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        history_dir = str(tmp_path / "history")

        add_result = runner.invoke(
            app,
            ["watch", "add", "--db-path", db_path, "--name", "my-trip", *self._spec_args()],
        )
        assert add_result.exit_code == 0, add_result.output
        assert "Created watch" in add_result.output
        watch_id = add_result.output.split("Created watch ")[1].split(" ")[0]

        list_result = runner.invoke(app, ["watch", "list", "--db-path", db_path])
        assert list_result.exit_code == 0
        assert "my-trip" in list_result.output
        assert "never" in list_result.output  # not run yet

        run_result = runner.invoke(
            app,
            [
                "watch", "run", watch_id, "--db-path", db_path,
                "--provider", "fixtures", "--history-dir", history_dir,
            ],
        )
        assert run_result.exit_code == 0, run_result.output
        assert "my-trip" in run_result.output

        history_file = tmp_path / "history" / f"{watch_id}.jsonl"
        assert history_file.exists()

        report_result = runner.invoke(app, ["report", watch_id, "--db-path", db_path])
        assert report_result.exit_code == 0
        assert "my-trip" in report_result.output
        assert "Run history" in report_result.output

        rm_result = runner.invoke(app, ["watch", "rm", watch_id, "--db-path", db_path])
        assert rm_result.exit_code == 0
        assert "Removed watch" in rm_result.output

        # history file survives watch deletion -- it's a historical record
        assert history_file.exists()

    def test_watch_add_missing_required_flags_fails_cleanly(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(
            app, ["watch", "add", "--db-path", db_path, "--from", "LHR"]
        )
        assert result.exit_code == 1
        assert "missing required option" in result.output

    def test_watch_add_from_last_uses_previous_search(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        search_result = runner.invoke(app, ["search", *self._search_args()])
        assert search_result.exit_code == 0

        add_result = runner.invoke(
            app, ["watch", "add", "--db-path", db_path, "--from-last"]
        )
        assert add_result.exit_code == 0, add_result.output
        assert "Created watch" in add_result.output

    def test_watch_add_from_last_without_a_previous_search_fails_cleanly(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(app, ["watch", "add", "--db-path", db_path, "--from-last"])
        assert result.exit_code == 1
        assert "no previous search found" in result.output

    def test_watch_rm_unknown_watch_fails_cleanly(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(app, ["watch", "rm", "nonexistent", "--db-path", db_path])
        assert result.exit_code == 1
        assert "No such watch" in result.output

    def test_report_unknown_watch_fails_cleanly(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(app, ["report", "nonexistent", "--db-path", db_path])
        assert result.exit_code == 1
        assert "No such watch" in result.output

    def test_watch_run_with_no_watches_reports_that(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(app, ["watch", "run", "--db-path", db_path])
        assert result.exit_code == 0
        assert "No active watches" in result.output

    def test_watch_run_unknown_watch_id_fails_cleanly(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        result = runner.invoke(app, ["watch", "run", "nonexistent", "--db-path", db_path])
        assert result.exit_code == 1
        assert "No such watch" in result.output

    def test_watch_run_all_active_watches(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        history_dir = str(tmp_path / "history")
        for name in ("trip-a", "trip-b"):
            add_result = runner.invoke(
                app,
                ["watch", "add", "--db-path", db_path, "--name", name, *self._spec_args()],
            )
            assert add_result.exit_code == 0

        run_result = runner.invoke(
            app, ["watch", "run", "--db-path", db_path, "--history-dir", history_dir]
        )
        assert run_result.exit_code == 0
        assert "trip-a" in run_result.output
        assert "trip-b" in run_result.output

    def test_watch_add_from_wizard_when_no_flags_given(self, tmp_path):
        db_path = str(tmp_path / "db.sqlite")
        answers = (
            "LHR\nbarcelona\n2027-01-01\n2027-12-31\nthu\nsun\n3\n3\n\n"
            "2\n2000\nGBP\nnormal\nn\nn\nn\nn\n"
        )
        result = runner.invoke(app, ["watch", "add", "--db-path", db_path], input=answers)
        assert result.exit_code == 0, result.output
        assert "Created watch" in result.output


class TestWatchRunAlerts:
    def _add_fitting_watch(self, db_path: str, name: str = "alert-trip") -> str:
        # Wide window/nights, no weekday restriction -- see
        # TestSearchCommand._run for why: these tests need a reliable
        # fits-budget result, and real (and fixture) free-tier fare data
        # is too sparse for a tight weekday+exact-nights rule to reliably
        # match anything.
        add_result = runner.invoke(
            app,
            [
                "watch", "add", "--db-path", db_path, "--name", name,
                "--from", "LHR", "--to", "barcelona",
                "--window", "2027-01-01:2027-12-31",
                "--nights", "1-14", "--party", "2", "--budget", "2000",
            ],
        )
        assert add_result.exit_code == 0, add_result.output
        return add_result.output.split("Created watch ")[1].split(" ")[0]

    def test_fitting_package_with_no_smtp_config_reports_not_sent(self, tmp_path, monkeypatch):
        for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_TO"):
            monkeypatch.delenv(var, raising=False)
        db_path = str(tmp_path / "db.sqlite")
        watch_id = self._add_fitting_watch(db_path)

        result = runner.invoke(
            app,
            ["watch", "run", watch_id, "--db-path", db_path, "--history-dir", str(tmp_path / "h")],
        )
        assert result.exit_code == 0
        assert "no alert sent" in result.output

    def test_no_alerts_flag_skips_alert_check_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "secret")
        monkeypatch.setenv("ALERT_TO", "me@example.com")
        db_path = str(tmp_path / "db.sqlite")
        watch_id = self._add_fitting_watch(db_path)

        result = runner.invoke(
            app,
            [
                "watch", "run", watch_id, "--db-path", db_path,
                "--history-dir", str(tmp_path / "h"), "--no-alerts",
            ],
        )
        assert result.exit_code == 0
        assert "Alert email sent" not in result.output
        assert "no alert sent" not in result.output

    def test_fitting_package_with_smtp_config_sends_and_dedupes_on_rerun(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "secret")
        monkeypatch.setenv("ALERT_TO", "me@example.com")

        sent_messages = []

        class _FakeSmtp:
            def __init__(self, host, port):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def starttls(self):
                pass

            def login(self, username, password):
                pass

            def send_message(self, message):
                sent_messages.append(message)

        monkeypatch.setattr("holiday_tracker.alerts.email.smtplib.SMTP", _FakeSmtp)

        db_path = str(tmp_path / "db.sqlite")
        history_dir = str(tmp_path / "h")
        watch_id = self._add_fitting_watch(db_path)

        first_run = runner.invoke(
            app, ["watch", "run", watch_id, "--db-path", db_path, "--history-dir", history_dir]
        )
        assert first_run.exit_code == 0
        assert "Alert email sent" in first_run.output
        assert len(sent_messages) == 1

        # Re-running immediately hits the same package/fingerprint -> cooldown suppresses it.
        second_run = runner.invoke(
            app, ["watch", "run", watch_id, "--db-path", db_path, "--history-dir", history_dir]
        )
        assert second_run.exit_code == 0
        assert "Alert email sent" not in second_run.output
        assert len(sent_messages) == 1

    def test_smtp_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "secret")
        monkeypatch.setenv("ALERT_TO", "me@example.com")

        class _FailingSmtp:
            def __init__(self, host, port):
                raise OSError("connection refused")

        monkeypatch.setattr("holiday_tracker.alerts.email.smtplib.SMTP", _FailingSmtp)

        db_path = str(tmp_path / "db.sqlite")
        watch_id = self._add_fitting_watch(db_path)

        result = runner.invoke(
            app,
            ["watch", "run", watch_id, "--db-path", db_path, "--history-dir", str(tmp_path / "h")],
        )
        assert result.exit_code == 0
        assert "Failed to send alert email" in result.output
