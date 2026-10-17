"""Adapter reproduces the frozen fixtures' summary.json facts and the v0 scope rules."""

from pathlib import Path

import pytest

from migproto.memory import adapter

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def load(name):
    return adapter.load(EXAMPLES / name)


def failed(run, attempt=None):
    return [o["check_name"] for o in run["observations"]
            if o["status"] == "FAIL" and (attempt is None or o["attempt"] == attempt)]


@pytest.mark.parametrize("name,verdict,total,n_failed", [
    ("unsafe-rename", "REPRODUCED", 7, 0),
    ("scripted-sync-failure", "FAIL", 29, 9),
    ("scripted-staged-success", "PASS", 33, 0),
    ("live-ordering-revision", "PASS", 71, 1),
])
def test_fixture_facts(name, verdict, total, n_failed):
    run = load(name)
    assert run["meta"]["verdict"] == verdict
    assert len(run["observations"]) == total == run["summary"]["checks_total"]
    assert len(failed(run)) == n_failed
    assert sorted(failed(run)) == sorted(run["summary"]["checks_failed"])


def test_live_run_keeps_both_attempts():
    run = load("live-ordering-revision")
    by_attempt = {}
    for o in run["observations"]:
        by_attempt.setdefault(o["attempt"], []).append(o)
    assert set(by_attempt) == {"a1", "a2"}
    assert failed(run, "a1") == ["retirement_gate"]
    assert len(by_attempt["a2"]) == 41 and failed(run, "a2") == []
    attempts = {a["attempt"]: a for a in run["attempts"]}
    assert attempts["a1"]["ok"] is False and attempts["a2"]["ok"] is True
    assert {p["attempt"] for p in run["plans"]} == {"a1", "a2"}


def test_stage_and_app_scope_derived():
    run = load("scripted-sync-failure")
    obs = next(o for o in run["observations"] if o["check_name"] == "case.old_write_new_read")
    assert obs["stage_id"] == "deploy_new"
    assert obs["attempt"] == "a1" and obs["plan_id"] == "scripted-v1"
    assert [s["app_version"] for s in obs["steps"]] == ["old", "new-staged"]
    assert obs["scope_source"] == "derived_v0"
    assert obs["observation_id"] == f"scripted-sync-failure/a1/case.old_write_new_read#{obs['seq']}"


def test_missing_scope_is_unknown_not_guessed():
    run = load("scripted-sync-failure")
    by_stage = lambda s: [o for o in run["observations"] if o["stage_id"] == s]
    # Rule 4: no schema event for S0, deploy_new, retire_old -> unknown, not "unchanged".
    for stage in ("S0", "deploy_new", "retire_old"):
        assert by_stage(stage) and all(o["schema_state"] is None for o in by_stage(stage))
    assert all(o["schema_state"] for o in by_stage("expand"))
    # v0 cannot distinguish failure, refusal and error.
    assert all(o["outcome"] is None for o in run["observations"])
    # Database-level checks have no app version.
    db_level = next(o for o in run["observations"] if o["check_name"] == "deploy_new.columns_agree")
    assert db_level["steps"] == []


def test_unsafe_new_app_maps_to_new_unsafe():
    assert adapter._app_version("new", "unsafe") == "new-unsafe"
    assert adapter._app_version("new", "staged") == "new-staged"
    assert adapter._app_version(None, "staged") is None


def test_model_metadata_not_invented():
    live = load("live-ordering-revision")["meta"]
    assert live["model"] == "gpt-oss:120b-cloud" and live["dev_only_model"] is True
    for field in ("checkpoint_revision", "serving_backend", "hardware"):
        assert live[field] is None
    scripted = load("scripted-staged-success")["meta"]
    assert scripted["model"] is None


def test_claims_are_unstructured_v0():
    claims = load("live-ordering-revision")["claims"]
    assert claims and all(c["structured"] == 0 and c["evidence"] == [] for c in claims)
    assert any(c["role"] == "planner" and c["attempt"] == "a2" and c["kind"] == "revision_notes"
               for c in claims)
    assert all(c["attempt"] is None for c in claims if c["role"] == "investigator")
