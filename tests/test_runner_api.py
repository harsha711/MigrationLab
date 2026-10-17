import json
import re
from pathlib import Path

import pytest

from migproto import lock, runner_api, staged
from migproto.runlog import RunLog

PKG = Path(runner_api.__file__).parent


@pytest.fixture(autouse=True)
def lock_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGPROTO_LOCK_PATH", str(tmp_path / "exec.lock"))


@pytest.fixture
def log(tmp_path):
    return RunLog("unit", root=tmp_path)


def test_invalid_plan_is_refused_with_observation(log):
    seen = []
    report = runner_api.execute_plan(
        {"plan_id": "bad", "stages": [], "shell": "rm -rf /"},
        run_context=runner_api.RunContext(log, provenance="agent:test", progress=seen.append))
    assert report.verdict == "refused" and report.ok is False
    assert report.halted_step == "validate"
    assert report.attempt_id == f"{log.run_id}/a1"
    assert report.outcome_counts["refused"] == 1
    [obs] = report.observations
    assert obs["name"] == "plan.schema_valid" and obs["check_type"] == "safety_gate"
    assert obs["provenance"] == "agent:test" and obs["executed_by"] == "runner"
    assert report.failed_checks[0]["observation_id"] == obs["observation_id"]
    assert "run_id" not in report.failed_checks[0]  # scope keys trimmed for feedback
    assert report.database["run_db"] is None and "plan" not in report.artifacts
    assert [e["kind"] for e in seen] == ["check"]
    assert not log.listeners and not lock.held()


def test_as_dict_keeps_v0_keys_first_and_omits_observations(log):
    report = runner_api.execute_plan({"plan_id": "bad"},
                                     run_context=runner_api.RunContext(log))
    d = report.as_dict()
    assert list(d)[:7] == ["ok", "plan_id", "completed_all_stages", "halted", "stages",
                           "failed_checks", "run_db"]
    assert "observations" not in d
    assert len(report.as_dict(include_observations=True)["observations"]) == 1
    json.dumps(d)


def test_attempt_override_and_report_from_stubbed_runner(log, monkeypatch):
    def fake_run(log, plan, tests, provenance, keep_db):
        log.check("retirement_gate", False, check_type="safety_gate", app_version="old")
        log.skip("stage.retire_old.not_run", "halted")
        return {"ok": False, "plan_id": plan["plan_id"], "completed_all_stages": False,
                "halted": "refused", "halted_step": "contract", "stages": [],
                "failed_checks": [], "run_db": "migproto_run_x", "db_dropped": True}
    monkeypatch.setattr(staged, "run", fake_run)
    report = runner_api.execute_plan({"plan_id": "p"},
                                     run_context=runner_api.RunContext(log, attempt=2))
    assert report.attempt_id.endswith("/a2")
    assert report.verdict == "refused"
    assert report.outcome_counts == {"pass": 0, "fail": 0, "refused": 1, "error": 0,
                                     "skipped": 1}
    assert report.artifacts["plan"] == "plan-p.json"
    assert report.database["dropped"] is True


@pytest.mark.parametrize(
    ("ok", "counts", "completed", "verdict"),
    [
        (True, {}, True, "pass"),
        (False, {"fail": 2, "refused": 1}, False, "fail"),
        (False, {"refused": 1}, False, "refused"),
        (False, {"error": 1, "fail": 3}, False, "error"),
        (False, {}, False, "error"),
    ],
)
def test_verdict_precedence(ok, counts, completed, verdict):
    assert runner_api._verdict(ok, counts, completed) == verdict


def test_lock_busy_propagates(log, tmp_path):
    lock.acquire("other-thread-holder")
    import threading
    errs = []
    t = threading.Thread(target=lambda: _call(log, errs))
    t.start()
    t.join()
    lock.release()
    assert errs and isinstance(errs[0], lock.ExecutionBusy)


def _call(log, errs):
    try:
        runner_api.execute_plan({"plan_id": "p"}, run_context=runner_api.RunContext(log))
    except lock.ExecutionBusy as exc:
        errs.append(exc)


def test_single_execution_path():
    callers = [p for p in PKG.rglob("*.py")
               if re.search(r"staged\.run\(", p.read_text()) and p.name != "runner_api.py"]
    assert callers == []
