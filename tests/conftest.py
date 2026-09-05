"""Shared pytest fixtures.

The autouse fixture below is a safety net, not a convenience: several CLI
commands (search, watch add/list/rm/run, report) read or write local state
(an HTTP cache, the last search, the watch database) that defaults to
~/.holiday-tracker/ on a real machine. Without this, running the test
suite would create and mutate files under the *actual* user's home
directory -- surprising at best, and actively wrong in CI or on a
developer's own machine. Every test gets its own isolated, throwaway state
directory instead, whether or not it knows it needs one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLIDAY_TRACKER_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOLIDAY_TRACKER_DB", str(tmp_path / "state" / "db.sqlite"))
    monkeypatch.setenv("HOLIDAY_TRACKER_HISTORY_DIR", str(tmp_path / "history"))
    yield
