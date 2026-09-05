-- SQLite schema for holiday-tracker's local watch/run history store.
--
-- Deliberately denormalized relative to the project plan's original
-- sketch: packages carry their flights/accommodation/spend breakdown
-- directly rather than through a separate "observations" table, since the
-- common read (a watch's trend over time) wants per-package totals, not
-- per-component rows, and the append-only data/history/<watch>.jsonl file
-- (see store/repo.py's append_price_history) is the other half of "price
-- history" the project plan describes -- this database is the queryable,
-- richer-but-ephemeral half, never committed to the repo.

CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    budget_minor_units INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id TEXT NOT NULL REFERENCES watches(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requests_used INTEGER NOT NULL DEFAULT 0,
    candidates_scanned INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    destination_city_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    nights INTEGER NOT NULL,
    flights_minor_units INTEGER NOT NULL,
    accommodation_minor_units INTEGER NOT NULL,
    spend_minor_units INTEGER NOT NULL,
    total_minor_units INTEGER NOT NULL,
    currency TEXT NOT NULL,
    fits_budget INTEGER NOT NULL,
    stay_json TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id TEXT NOT NULL REFERENCES watches(id),
    package_fingerprint TEXT NOT NULL,
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_watch ON runs(watch_id);
CREATE INDEX IF NOT EXISTS idx_packages_run ON packages(run_id);
CREATE INDEX IF NOT EXISTS idx_alerts_watch_fingerprint ON alerts(watch_id, package_fingerprint);
