"""Storage operations for watches, their run history, and alert dedup state,
built on top of store/db.py's SQLite connection.

Also owns append_price_history(), which writes the append-only
data/history/<watch>.jsonl files the project plan describes as the
committed, versioned half of "price history" -- distinct from (and
simpler than) the richer SQLite run/package rows, which stay local.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from holiday_tracker.engine.search import SearchResults
from holiday_tracker.models import Money, SearchSpec


@dataclass(frozen=True)
class WatchRecord:
    id: str
    name: str
    spec: SearchSpec
    status: str
    created_at: datetime
    last_run_at: datetime | None


@dataclass(frozen=True)
class PackageRecord:
    destination_city_id: str
    origin: str
    depart_date: date
    return_date: date
    nights: int
    flights_cost: Money
    accommodation_cost: Money
    spend_cost: Money
    total_cost: Money
    fits_budget: bool


@dataclass(frozen=True)
class RunRecord:
    id: int
    watch_id: str
    started_at: datetime
    finished_at: datetime | None
    requests_used: int
    candidates_scanned: int
    error: str | None
    packages: list[PackageRecord]

    @property
    def best_package(self) -> PackageRecord | None:
        if not self.packages:
            return None
        return min(self.packages, key=lambda p: p.total_cost.minor_units)


def _row_to_watch(row: sqlite3.Row) -> WatchRecord:
    return WatchRecord(
        id=row["id"],
        name=row["name"],
        spec=SearchSpec.model_validate_json(row["spec_json"]),
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
    )


def _row_to_package(row: sqlite3.Row) -> PackageRecord:
    currency = row["currency"]
    return PackageRecord(
        destination_city_id=row["destination_city_id"],
        origin=row["origin"],
        depart_date=date.fromisoformat(row["depart_date"]),
        return_date=date.fromisoformat(row["return_date"]),
        nights=row["nights"],
        flights_cost=Money(minor_units=row["flights_minor_units"], currency=currency),
        accommodation_cost=Money(minor_units=row["accommodation_minor_units"], currency=currency),
        spend_cost=Money(minor_units=row["spend_minor_units"], currency=currency),
        total_cost=Money(minor_units=row["total_minor_units"], currency=currency),
        fits_budget=bool(row["fits_budget"]),
    )


# --------------------------------------------------------------------------
# Watches
# --------------------------------------------------------------------------


def create_watch(conn: sqlite3.Connection, spec: SearchSpec, name: str) -> WatchRecord:
    watch_id = uuid.uuid4().hex[:12]
    created_at = datetime.now()
    conn.execute(
        "INSERT INTO watches (id, name, spec_json, budget_minor_units, currency, status, "
        "created_at, last_run_at) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL)",
        (
            watch_id,
            name,
            spec.model_dump_json(),
            spec.budget.minor_units,
            spec.budget.currency,
            created_at.isoformat(),
        ),
    )
    conn.commit()
    return WatchRecord(
        id=watch_id, name=name, spec=spec, status="active", created_at=created_at, last_run_at=None
    )


def list_watches(conn: sqlite3.Connection) -> list[WatchRecord]:
    rows = conn.execute("SELECT * FROM watches ORDER BY created_at").fetchall()
    return [_row_to_watch(row) for row in rows]


def get_watch(conn: sqlite3.Connection, watch_id: str) -> WatchRecord | None:
    row = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return _row_to_watch(row) if row else None


def delete_watch(conn: sqlite3.Connection, watch_id: str) -> bool:
    """Remove a watch and its run/package/alert history. Does not touch
    data/history/<watch_id>.jsonl -- that committed file is an intentional
    historical record and outlives the watch config that produced it."""
    if get_watch(conn, watch_id) is None:
        return False
    conn.execute(
        "DELETE FROM packages WHERE run_id IN (SELECT id FROM runs WHERE watch_id = ?)",
        (watch_id,),
    )
    conn.execute("DELETE FROM runs WHERE watch_id = ?", (watch_id,))
    conn.execute("DELETE FROM alerts WHERE watch_id = ?", (watch_id,))
    conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
    conn.commit()
    return True


def set_watch_status(conn: sqlite3.Connection, watch_id: str, status: str) -> None:
    conn.execute("UPDATE watches SET status = ? WHERE id = ?", (status, watch_id))
    conn.commit()


# --------------------------------------------------------------------------
# Runs and packages
# --------------------------------------------------------------------------


def record_run(
    conn: sqlite3.Connection,
    watch_id: str,
    *,
    started_at: datetime,
    finished_at: datetime,
    requests_used: int,
    results: SearchResults | None,
    error: str | None = None,
) -> RunRecord:
    """Persist one search run for a watch: the run metadata, every priced
    package it produced, and a bump of the watch's last_run_at."""
    cursor = conn.execute(
        "INSERT INTO runs (watch_id, started_at, finished_at, requests_used, "
        "candidates_scanned, error) VALUES (?, ?, ?, ?, ?, ?)",
        (
            watch_id,
            started_at.isoformat(),
            finished_at.isoformat(),
            requests_used,
            len(results.packages) if results else 0,
            error,
        ),
    )
    run_id = cursor.lastrowid

    packages: list[PackageRecord] = []
    for package in (results.packages if results else []):
        conn.execute(
            "INSERT INTO packages (run_id, destination_city_id, origin, depart_date, "
            "return_date, nights, flights_minor_units, accommodation_minor_units, "
            "spend_minor_units, total_minor_units, currency, fits_budget) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                package.destination_city_id,
                package.origin,
                package.depart_date.isoformat(),
                package.return_date.isoformat(),
                package.nights,
                package.flights_cost.minor_units,
                package.accommodation_cost.minor_units,
                package.spend_cost.minor_units,
                package.total_cost.minor_units,
                package.total_cost.currency,
                1 if package.fits_budget else 0,
            ),
        )
        packages.append(
            PackageRecord(
                destination_city_id=package.destination_city_id,
                origin=package.origin,
                depart_date=package.depart_date,
                return_date=package.return_date,
                nights=package.nights,
                flights_cost=package.flights_cost,
                accommodation_cost=package.accommodation_cost,
                spend_cost=package.spend_cost,
                total_cost=package.total_cost,
                fits_budget=package.fits_budget,
            )
        )

    conn.execute(
        "UPDATE watches SET last_run_at = ? WHERE id = ?", (finished_at.isoformat(), watch_id)
    )
    conn.commit()

    return RunRecord(
        id=run_id,
        watch_id=watch_id,
        started_at=started_at,
        finished_at=finished_at,
        requests_used=requests_used,
        candidates_scanned=len(packages),
        error=error,
        packages=packages,
    )


def run_history(conn: sqlite3.Connection, watch_id: str) -> list[RunRecord]:
    """Every recorded run for a watch, oldest first, each with its packages."""
    run_rows = conn.execute(
        "SELECT * FROM runs WHERE watch_id = ? ORDER BY started_at", (watch_id,)
    ).fetchall()
    history: list[RunRecord] = []
    for run_row in run_rows:
        package_rows = conn.execute(
            "SELECT * FROM packages WHERE run_id = ? ORDER BY total_minor_units",
            (run_row["id"],),
        ).fetchall()
        history.append(
            RunRecord(
                id=run_row["id"],
                watch_id=watch_id,
                started_at=datetime.fromisoformat(run_row["started_at"]),
                finished_at=(
                    datetime.fromisoformat(run_row["finished_at"])
                    if run_row["finished_at"]
                    else None
                ),
                requests_used=run_row["requests_used"],
                candidates_scanned=run_row["candidates_scanned"],
                error=run_row["error"],
                packages=[_row_to_package(row) for row in package_rows],
            )
        )
    return history


def latest_run(conn: sqlite3.Connection, watch_id: str) -> RunRecord | None:
    history = run_history(conn, watch_id)
    return history[-1] if history else None


# --------------------------------------------------------------------------
# Alerts (storage only -- the fits-budget/cooldown decision logic is
# alerts/rules.py, phase 6)
# --------------------------------------------------------------------------


def record_alert(
    conn: sqlite3.Connection, watch_id: str, fingerprint: str, kind: str, sent_at: datetime
) -> None:
    conn.execute(
        "INSERT INTO alerts (watch_id, package_fingerprint, kind, sent_at) VALUES (?, ?, ?, ?)",
        (watch_id, fingerprint, kind, sent_at.isoformat()),
    )
    conn.commit()


def last_alert_at(conn: sqlite3.Connection, watch_id: str, fingerprint: str) -> datetime | None:
    row = conn.execute(
        "SELECT sent_at FROM alerts WHERE watch_id = ? AND package_fingerprint = ? "
        "ORDER BY sent_at DESC LIMIT 1",
        (watch_id, fingerprint),
    ).fetchone()
    return datetime.fromisoformat(row["sent_at"]) if row else None


# --------------------------------------------------------------------------
# Committed price history (data/history/<watch_id>.jsonl)
# --------------------------------------------------------------------------


def append_price_history(history_dir: str | Path, watch_id: str, run: RunRecord) -> Path:
    """Append one JSON line summarising `run` to data/history/<watch_id>.jsonl.

    This is the append-only, git-friendly dataset the scheduled GitHub
    Action (phase 7) commits back to the repo -- deliberately a much
    smaller summary than the full SQLite package rows, since it's meant to
    be read as a diffable trend, not a re-query-able database.
    """
    history_dir = Path(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    best = run.best_package
    record = {
        "run_id": run.id,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "requests_used": run.requests_used,
        "candidates_scanned": run.candidates_scanned,
        "error": run.error,
        "best_total": best.total_cost.amount if best else None,
        "best_currency": best.total_cost.currency if best else None,
        "best_destination_city_id": best.destination_city_id if best else None,
        "best_fits_budget": best.fits_budget if best else None,
    }
    path = history_dir / f"{watch_id}.jsonl"
    with path.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(record) + "\n")
    return path
