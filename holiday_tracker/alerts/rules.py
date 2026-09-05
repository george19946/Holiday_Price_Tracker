"""Decides whether a watch's latest run should trigger an alert email.

Fires when a run's best package fits the budget (satisfies every stay
filter too -- that's what Package.fits_budget already encodes, see
engine/pricing.py) and no alert has already been sent for that exact
package within the cooldown window. A genuinely different fitting package
-- a new destination, different dates, or a materially different price --
always alerts immediately; the cooldown only suppresses repeat
notifications about the *same* fare being re-observed run after run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from holiday_tracker.store.repo import PackageRecord, last_alert_at, record_alert

DEFAULT_COOLDOWN = timedelta(hours=72)
ALERT_KIND_FITS_BUDGET = "fits_budget"


def package_fingerprint(package: PackageRecord) -> str:
    """A stable identifier for "this same holiday", used to dedup alerts.

    Rounds the total to whole currency units -- a fare wobbling by pennies
    between provider cache refreshes isn't a meaningfully different
    holiday, and re-alerting on that noise would be worse than useless.
    """
    rounded_total = round(package.total_cost.amount)
    return f"{package.destination_city_id}:{package.depart_date}:{package.return_date}:{rounded_total}"


@dataclass(frozen=True)
class AlertDecision:
    should_send: bool
    package: PackageRecord | None
    fingerprint: str | None
    reason: str


def decide(
    conn: sqlite3.Connection,
    watch_id: str,
    best_package: PackageRecord | None,
    *,
    now: datetime | None = None,
    cooldown: timedelta = DEFAULT_COOLDOWN,
) -> AlertDecision:
    now = now or datetime.now()

    if best_package is None:
        return AlertDecision(False, None, None, "no candidates in this run")
    if not best_package.fits_budget:
        return AlertDecision(False, None, None, "best package is still over budget")

    fingerprint = package_fingerprint(best_package)
    last_sent = last_alert_at(conn, watch_id, fingerprint)
    if last_sent is not None and now - last_sent < cooldown:
        return AlertDecision(
            False,
            best_package,
            fingerprint,
            f"already alerted for this package at {last_sent} (cooldown active)",
        )

    return AlertDecision(True, best_package, fingerprint, "package fits budget")


def record_sent(
    conn: sqlite3.Connection, watch_id: str, fingerprint: str, *, now: datetime | None = None
) -> None:
    record_alert(conn, watch_id, fingerprint, ALERT_KIND_FITS_BUDGET, now or datetime.now())
