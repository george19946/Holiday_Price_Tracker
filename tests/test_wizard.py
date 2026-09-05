"""Tests for wizard.py: the interactive quick-list flow.

Uses a ScriptedConsole (a Console subclass whose .input() pops from a
canned list of answers instead of reading real stdin) to drive run_wizard()
deterministically -- this only works because every prompt call in
wizard.py passes console=console explicitly rather than relying on rich's
default global console. If a future edit drops one of those console=
kwargs, these tests hang or read from the wrong place, which is exactly
the regression they exist to catch.
"""

from __future__ import annotations

import io

from rich.console import Console

from holiday_tracker.models import SpendStyle, Weekday
from holiday_tracker.wizard import run_wizard


class ScriptedConsole(Console):
    def __init__(self, answers: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._answers = list(answers)

    def input(self, *args, **kwargs) -> str:
        return self._answers.pop(0)


def _console(answers: list[str]) -> ScriptedConsole:
    return ScriptedConsole(answers, file=io.StringIO())


FULL_ANSWERS = [
    "LHR,MAN",  # origins
    "barcelona",  # destination
    "2027-01-01",  # window start
    "2027-12-31",  # window end
    "thu",  # depart dow
    "sun",  # return dow
    "3",  # nights min
    "4",  # nights max
    "2027-12-20:2028-01-03",  # blackout entry
    "",  # blackout: blank to finish
    "2",  # party size
    "500",  # budget
    "GBP",  # currency
    "normal",  # spend style
    "y",  # exclude hostels
    "y",  # set min rating?
    "8",  # min rating value
    "n",  # set max distance?
    "y",  # free cancellation only
]


def test_full_wizard_run_produces_expected_search_spec():
    spec = run_wizard(console=_console(FULL_ANSWERS))

    assert spec.origins == ["LHR", "MAN"]
    assert spec.destination == "barcelona"
    assert spec.party_size == 2
    assert spec.budget.amount == 500
    assert spec.budget.currency == "GBP"
    assert spec.spend_style == SpendStyle.normal

    assert spec.date_rule.window_start.isoformat() == "2027-01-01"
    assert spec.date_rule.window_end.isoformat() == "2027-12-31"
    assert spec.date_rule.depart_dow == {Weekday.thu}
    assert spec.date_rule.return_dow == {Weekday.sun}
    assert spec.date_rule.nights_min == 3
    assert spec.date_rule.nights_max == 4
    assert len(spec.date_rule.blackouts) == 1

    assert spec.stay_filters.exclude_hostels is True
    assert spec.stay_filters.min_rating == 8.0
    assert spec.stay_filters.max_centre_km is None
    assert spec.stay_filters.free_cancellation_only is True


def test_no_stay_filters_selected_leaves_them_permissive():
    answers = [
        "LHR", "barcelona", "2027-01-01", "2027-12-31", "any", "any",
        "3", "3", "",  # no blackouts
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",  # every stay filter question declined
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.stay_filters.exclude_hostels is False
    assert spec.stay_filters.min_rating is None
    assert spec.stay_filters.max_centre_km is None
    assert spec.stay_filters.free_cancellation_only is False


def test_invalid_destination_is_reprompted():
    answers = [
        "LHR",
        "Atlantis",  # invalid -- reprompted
        "barcelona",  # valid
        "2027-01-01", "2027-12-31", "any", "any",
        "3", "3", "",
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.destination == "barcelona"


def test_invalid_window_is_reprompted():
    answers = [
        "LHR",
        "barcelona",
        "not-a-date", "also-not-a-date",  # invalid window, reprompted
        "2027-01-01", "2027-12-31",  # valid window
        "any", "any",
        "3", "3", "",
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.date_rule.window_start.isoformat() == "2027-01-01"


def test_window_start_after_end_is_reprompted():
    answers = [
        "LHR",
        "barcelona",
        "2027-12-31", "2027-01-01",  # start after end, reprompted
        "2027-01-01", "2027-12-31",  # valid order
        "any", "any",
        "3", "3", "",
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.date_rule.window_start.isoformat() == "2027-01-01"
    assert spec.date_rule.window_end.isoformat() == "2027-12-31"


def test_unrecognised_weekday_falls_back_to_any():
    answers = [
        "LHR", "barcelona", "2027-01-01", "2027-12-31",
        "notaday",  # falls back to "any" (empty set) with a warning
        "any",
        "3", "3", "",
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.date_rule.depart_dow == set()


def test_nights_min_greater_than_max_is_normalized():
    answers = [
        "LHR", "barcelona", "2027-01-01", "2027-12-31", "any", "any",
        "5", "3",  # min > max as entered
        "",
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.date_rule.nights_min == 3
    assert spec.date_rule.nights_max == 5


def test_multiple_blackouts_and_single_date_blackout():
    answers = [
        "LHR", "barcelona", "2027-01-01", "2027-12-31", "any", "any",
        "3", "3",
        "2027-06-15", "2027-12-20:2028-01-03", "",  # two blackouts, then finish
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert len(spec.date_rule.blackouts) == 2
    assert (spec.date_rule.blackouts[0][0].isoformat() == "2027-06-15")


def test_malformed_blackout_entry_is_reprompted():
    answers = [
        "LHR", "barcelona", "2027-01-01", "2027-12-31", "any", "any",
        "3", "3",
        "not-a-date",  # malformed, reprompted
        "",  # finish with no blackouts
        "1", "500", "GBP", "normal",
        "n", "n", "n", "n",
    ]
    spec = run_wizard(console=_console(answers))
    assert spec.date_rule.blackouts == []
