"""SQLite connection management for the local watch/run history store.

The database lives outside the repository (default: ~/.holiday-tracker/db.sqlite)
-- it's per-machine ephemeral state, never committed. The append-only
data/history/<watch>.jsonl files (store/repo.py's append_price_history)
are the versioned, git-committed half of "price history"; this database is
the queryable half, rebuildable at any time by re-running watches.
"""

from __future__ import annotations

import os
import sqlite3
from importlib import resources
from pathlib import Path

_PACKAGE = "holiday_tracker.store"
_ENV_OVERRIDE = "HOLIDAY_TRACKER_DB"


def default_db_path() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path.home() / ".holiday-tracker" / "db.sqlite"


def _schema_sql() -> str:
    return resources.files(_PACKAGE).joinpath("schema.sql").read_text(encoding="utf-8")


def get_connection(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the watch/run history database at `path`,
    or the default location if omitted, and ensure its schema exists.

    Returns rows as sqlite3.Row (dict-like access by column name) so
    store/repo.py doesn't have to track column positions by hand.
    """
    resolved = Path(path) if path is not None else default_db_path()
    if str(resolved) != ":memory:":
        resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.executescript(_schema_sql())
    return conn
