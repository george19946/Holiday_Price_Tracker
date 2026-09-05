"""Tests for store/db.py and store/repo.py: the local watch/run history store."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from holiday_tracker.engine.search import run_search
from holiday_tracker.models import DateRule, Money, SearchSpec
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider
from holiday_tracker.store import db, repo


def _spec(budget: float = 2000) -> SearchSpec:
    return SearchSpec(
        origins=["LHR"],
        destination="barcelona",
        date_rule=DateRule(
            window_start=date(2027, 3, 1), window_end=date(2027, 5, 31), nights_min=3, nights_max=3
        ),
        budget=Money.from_major(budget, "GBP"),
        party_size=2,
    )


@pytest.fixture
def conn():
    connection = db.get_connection(":memory:")
    yield connection
    connection.close()


class TestDb:
    def test_get_connection_applies_schema(self, conn):
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"watches", "runs", "packages", "alerts"} <= tables

    def test_default_db_path_respects_env_override(self, monkeypatch, tmp_path):
        override = tmp_path / "custom.sqlite"
        monkeypatch.setenv("HOLIDAY_TRACKER_DB", str(override))
        assert db.default_db_path() == override

    def test_default_db_path_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("HOLIDAY_TRACKER_DB", raising=False)
        assert ".holiday-tracker" in str(db.default_db_path())


class TestWatchCrud:
    def test_create_and_get_watch(self, conn):
        spec = _spec()
        watch = repo.create_watch(conn, spec, "my-trip")
        fetched = repo.get_watch(conn, watch.id)
        assert fetched is not None
        assert fetched.name == "my-trip"
        assert fetched.spec.destination == "barcelona"
        assert fetched.status == "active"
        assert fetched.last_run_at is None

    def test_get_unknown_watch_returns_none(self, conn):
        assert repo.get_watch(conn, "nope") is None

    def test_list_watches_ordered_by_creation(self, conn):
        a = repo.create_watch(conn, _spec(), "a")
        b = repo.create_watch(conn, _spec(), "b")
        listed = repo.list_watches(conn)
        assert [w.id for w in listed] == [a.id, b.id]

    def test_delete_watch_returns_true_and_removes_it(self, conn):
        watch = repo.create_watch(conn, _spec(), "gone-soon")
        assert repo.delete_watch(conn, watch.id) is True
        assert repo.get_watch(conn, watch.id) is None

    def test_delete_unknown_watch_returns_false(self, conn):
        assert repo.delete_watch(conn, "nope") is False

    def test_set_watch_status(self, conn):
        watch = repo.create_watch(conn, _spec(), "pausable")
        repo.set_watch_status(conn, watch.id, "paused")
        assert repo.get_watch(conn, watch.id).status == "paused"

    def test_spec_round_trips_through_json(self, conn):
        spec = _spec()
        watch = repo.create_watch(conn, spec, "round-trip")
        fetched = repo.get_watch(conn, watch.id)
        assert fetched.spec.model_dump() == spec.model_dump()


class TestRuns:
    def test_record_run_persists_packages_and_updates_last_run_at(self, conn):
        spec = _spec()
        watch = repo.create_watch(conn, spec, "watch-1")
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())

        started = datetime(2026, 1, 1, 9, 0, 0)
        finished = datetime(2026, 1, 1, 9, 0, 5)
        run = repo.record_run(
            conn, watch.id, started_at=started, finished_at=finished,
            requests_used=3, results=results,
        )
        assert run.packages
        assert run.best_package is not None

        refreshed = repo.get_watch(conn, watch.id)
        assert refreshed.last_run_at == finished

    def test_record_run_with_error_stores_no_packages(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-err")
        run = repo.record_run(
            conn, watch.id, started_at=datetime.now(), finished_at=datetime.now(),
            requests_used=0, results=None, error="provider outage",
        )
        assert run.packages == []
        assert run.error == "provider outage"
        assert run.best_package is None

    def test_run_history_returns_runs_oldest_first(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-hist")
        results = run_search(_spec(), FixturesFlightProvider(), FixturesStayProvider())
        first = repo.record_run(
            conn, watch.id, started_at=datetime(2026, 1, 1), finished_at=datetime(2026, 1, 1),
            requests_used=1, results=results,
        )
        second = repo.record_run(
            conn, watch.id, started_at=datetime(2026, 1, 2), finished_at=datetime(2026, 1, 2),
            requests_used=1, results=results,
        )
        history = repo.run_history(conn, watch.id)
        assert [r.id for r in history] == [first.id, second.id]

    def test_latest_run_returns_the_most_recent(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-latest")
        results = run_search(_spec(), FixturesFlightProvider(), FixturesStayProvider())
        repo.record_run(
            conn, watch.id, started_at=datetime(2026, 1, 1), finished_at=datetime(2026, 1, 1),
            requests_used=1, results=results,
        )
        second = repo.record_run(
            conn, watch.id, started_at=datetime(2026, 1, 2), finished_at=datetime(2026, 1, 2),
            requests_used=1, results=results,
        )
        assert repo.latest_run(conn, watch.id).id == second.id

    def test_latest_run_with_no_runs_returns_none(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-empty")
        assert repo.latest_run(conn, watch.id) is None

    def test_best_package_is_the_cheapest(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-best")
        results = run_search(_spec(), FixturesFlightProvider(), FixturesStayProvider())
        run = repo.record_run(
            conn, watch.id, started_at=datetime.now(), finished_at=datetime.now(),
            requests_used=1, results=results,
        )
        cheapest = min(p.total_cost.minor_units for p in run.packages)
        assert run.best_package.total_cost.minor_units == cheapest


class TestAlertsStorage:
    def test_no_alert_recorded_returns_none(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-alerts")
        assert repo.last_alert_at(conn, watch.id, "fingerprint-1") is None

    def test_records_and_retrieves_most_recent_alert(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-alerts-2")
        repo.record_alert(conn, watch.id, "fp", "fits_budget", datetime(2026, 1, 1))
        repo.record_alert(conn, watch.id, "fp", "fits_budget", datetime(2026, 1, 5))
        assert repo.last_alert_at(conn, watch.id, "fp") == datetime(2026, 1, 5)

    def test_different_fingerprints_are_independent(self, conn):
        watch = repo.create_watch(conn, _spec(), "watch-alerts-3")
        repo.record_alert(conn, watch.id, "fp-a", "fits_budget", datetime(2026, 1, 1))
        assert repo.last_alert_at(conn, watch.id, "fp-b") is None


class TestPriceHistoryFile:
    def test_append_price_history_writes_one_json_line(self, conn, tmp_path):
        watch = repo.create_watch(conn, _spec(), "watch-jsonl")
        results = run_search(_spec(), FixturesFlightProvider(), FixturesStayProvider())
        run = repo.record_run(
            conn, watch.id, started_at=datetime.now(), finished_at=datetime.now(),
            requests_used=3, results=results,
        )
        path = repo.append_price_history(tmp_path, watch.id, run)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["best_destination_city_id"] == "barcelona"
        assert record["best_fits_budget"] is True

    def test_append_price_history_appends_not_overwrites(self, conn, tmp_path):
        watch = repo.create_watch(conn, _spec(), "watch-jsonl-2")
        results = run_search(_spec(), FixturesFlightProvider(), FixturesStayProvider())
        for _ in range(2):
            run = repo.record_run(
                conn, watch.id, started_at=datetime.now(), finished_at=datetime.now(),
                requests_used=1, results=results,
            )
            repo.append_price_history(tmp_path, watch.id, run)
        path = tmp_path / f"{watch.id}.jsonl"
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_append_price_history_handles_no_packages(self, conn, tmp_path):
        watch = repo.create_watch(conn, _spec(), "watch-jsonl-empty")
        run = repo.record_run(
            conn, watch.id, started_at=datetime.now(), finished_at=datetime.now(),
            requests_used=0, results=None, error="boom",
        )
        path = repo.append_price_history(tmp_path, watch.id, run)
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["best_total"] is None
        assert record["error"] == "boom"
