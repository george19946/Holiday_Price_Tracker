"""Guards the .github/workflows/*.yml files against accidental breakage.

Not a substitute for actually running them on GitHub, but catches the
easy-to-introduce mistakes (bad indentation, an unclosed quote in a `run:`
block, a typo'd job/step key) well before a push finds out the hard way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


def test_ci_workflow_is_valid_yaml_with_expected_jobs():
    doc = _load("ci.yml")
    assert "test" in doc["jobs"]


def test_track_workflow_is_valid_yaml_with_expected_jobs():
    doc = _load("track.yml")
    assert "track" in doc["jobs"]


def test_track_workflow_declares_required_permissions_and_triggers():
    doc = _load("track.yml")
    assert doc["permissions"]["contents"] == "write"
    # PyYAML parses the bare "on" key as the boolean True under YAML 1.1 --
    # a well-known cosmetic quirk with GitHub Actions files that doesn't
    # affect GitHub's own parser, so we check for either spelling.
    triggers = doc.get("on", doc.get(True))
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers


def test_track_workflow_bootstrap_inputs_cover_the_core_search_flags():
    doc = _load("track.yml")
    triggers = doc.get("on", doc.get(True))
    inputs = triggers["workflow_dispatch"]["inputs"]
    for required_input in ("add_from", "add_to", "add_window", "add_nights", "add_budget"):
        assert required_input in inputs

    step_names = [s.get("name") for s in doc["jobs"]["track"]["steps"]]
    assert "Bootstrap a watch (manual trigger only)" in step_names
    assert "Run watches" in step_names
    assert "Commit updated price history" in step_names


def test_track_workflow_run_scripts_are_syntactically_valid_bash():
    doc = _load("track.yml")
    for step in doc["jobs"]["track"]["steps"]:
        script = step.get("run")
        if script is None:
            continue
        result = subprocess.run(
            ["bash", "-n"], input=script, text=True, capture_output=True
        )
        assert result.returncode == 0, (
            f"step {step.get('name')!r} has invalid bash:\n{result.stderr}"
        )


def test_track_workflow_uses_env_vars_not_inline_interpolation_for_workflow_dispatch_inputs():
    """Passing untrusted workflow_dispatch input strings straight into a
    `${{ }}`-interpolated shell command is a known GitHub Actions
    script-injection footgun. This asserts the bootstrap step instead
    reads them through env: (as "$VAR"), never as literal `${{ inputs.* }}`
    inside the run script itself.
    """
    doc = _load("track.yml")
    bootstrap = next(
        s
        for s in doc["jobs"]["track"]["steps"]
        if s.get("name") == "Bootstrap a watch (manual trigger only)"
    )
    assert "${{" not in bootstrap["run"]
    assert set(bootstrap.get("env", {})) >= {
        "ADD_FROM", "ADD_TO", "ADD_WINDOW", "ADD_NIGHTS", "ADD_BUDGET",
    }
