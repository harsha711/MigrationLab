import json
import re
from pathlib import Path

import pytest

from migproto import checks
from migproto.runlog import CONTRACT_VERSION, RunLog


@pytest.fixture
def log(tmp_path):
    return RunLog("unit", root=tmp_path)


def lines(log):
    return [json.loads(x) for x in (log.dir / "events.jsonl").read_text().splitlines()]


def test_check_keeps_v0_keys_and_adds_scope(log):
    log.scope["app_commits"] = {"old": "abc", "new": "def"}
    log.scope["schema_state"] = {"schema_hash": "h"}
    log.scope["database_snapshot_id"] = "seed:x"
    log.set_step("deploy_new")
    log.check("case.x", True, provenance="manual", steps=[], app_version="both",
              expected={"a": 1}, observed={"a": 1})
    rec = lines(log)[0]
    for key in ("t", "kind", "provenance", "name", "status", "steps"):
        assert key in rec
    assert rec["status"] == "PASS" and rec["kind"] == "check" and rec["provenance"] == "manual"
    assert rec["contract_version"] == CONTRACT_VERSION
    assert rec["run_id"] == log.dir.name
    assert rec["attempt_id"] == f"{log.dir.name}/a1"
    assert rec["step_id"] == "deploy_new"
    assert rec["observation_id"] == f"{log.dir.name}/a1/case.x#0"
    assert rec["outcome"] == "pass" and rec["check_type"] == "assertion"
    assert rec["app_commit"] == {"old": "abc", "new": "def"}
    assert rec["schema_state"] == {"schema_hash": "h"}
    assert rec["database_snapshot_id"] == "seed:x"
    assert rec["executed_by"] == "runner"
    assert re.match(r"\d{4}-\d\d-\d\dT", rec["timestamp"])


def test_unknown_scope_stays_null(log):
    log.check("plan.schema_valid", True, check_type="safety_gate")
    rec = lines(log)[0]
    for key in ("app_version", "app_commit", "schema_state", "database_snapshot_id", "step_id",
                "expected", "observed"):
        assert rec[key] is None


def test_single_app_commit_is_not_both(log):
    log.scope["app_commits"] = {"old": "abc", "new": "def"}
    log.check("x", True, app_version="old")
    log.check("y", True, app_version="database")
    old, dbrec = lines(log)
    assert old["app_commit"] == {"old": "abc"}
    assert dbrec["app_commit"] is None


@pytest.mark.parametrize(
    ("check_type", "outcome", "status"),
    [("assertion", "fail", "FAIL"), ("safety_gate", "refused", "FAIL"),
     ("execution", "error", "FAIL")],
)
def test_failure_outcome_by_check_type(log, check_type, outcome, status):
    log.check("x", False, check_type=check_type)
    rec = lines(log)[0]
    assert (rec["outcome"], rec["status"]) == (outcome, status)


def test_explicit_error_outcome_and_skip(log):
    log.check("case.y", False, outcome="error")
    log.skip("stage.c.not_run", "halted")
    err, skip = lines(log)
    assert (err["outcome"], err["status"]) == ("error", "FAIL")
    assert (skip["outcome"], skip["status"]) == ("skipped", "SKIP")
    s = log.summary("FAIL")
    assert s["checks_failed"] == ["case.y"]
    assert s["outcome_counts"][log.attempt_id]["skipped"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [{"check_type": "vibes"}, {"app_version": "new-staged"}, {"outcome": "pass"}],
)
def test_check_rejects_bad_values(log, kwargs):
    with pytest.raises(ValueError):
        log.check("x", False, **kwargs)


def test_observation_ids_unique_and_attempt_scoped(log):
    log.check("retirement_gate", False, check_type="safety_gate")
    log.event("attempt", name="attempt 1")
    log.check("retirement_gate", True, check_type="safety_gate")
    log.event("attempt", name="attempt 2")
    log.check("retirement_gate", True, check_type="safety_gate")
    recs = [r for r in lines(log) if r["kind"] == "check"]
    ids = [r["observation_id"] for r in recs]
    assert len(set(ids)) == 3
    assert [r["attempt_id"].rsplit("/", 1)[1] for r in recs] == ["a1", "a1", "a2"]
    counts = log.summary("PASS")["outcome_counts"]
    assert counts[f"{log.run_id}/a1"]["refused"] == 1
    assert counts[f"{log.run_id}/a2"]["pass"] == 1


def test_new_attempt_clears_scope(log):
    log.scope["schema_state"] = {"schema_hash": "h"}
    log.set_step("contract")
    log.event("attempt", name="attempt 1")
    log.event("attempt", name="attempt 2")
    log.check("x", True)
    rec = lines(log)[-1]
    assert rec["schema_state"] is None and rec["step_id"] is None


def test_progress_listener_gets_every_event_and_cannot_break_logging(log):
    seen = []
    log.listeners.append(seen.append)
    log.listeners.append(lambda r: 1 / 0)
    log.event("stage", stage="expand")
    log.check("x", True)
    assert [r["kind"] for r in seen] == ["stage", "check"]
    assert len(lines(log)) == 2


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (checks.CATALOGUE["old_write_new_read"], "both"),
        ([{"app": "old", "op": "write", "user": "S"}, {"app": "old", "op": "read", "user": "S"}],
         "old"),
        ([{"app": "new", "op": "read", "user": "S"}], "new"),
        ([], None),
    ],
)
def test_case_app_version(steps, expected):
    assert checks.case_app_version(steps) == expected


def test_agents_never_create_observations():
    for path in (Path(checks.__file__).parent / "agents").glob("*.py"):
        text = path.read_text()
        assert not re.search(r"\.check\(|\.skip\(", text), f"{path} records an observation"
