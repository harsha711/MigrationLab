"""Gym verify: plan extraction, validation, runner call and reward rule (staged.run mocked)."""

import json

import pytest

from migproto import scenarios, staged
from migproto.runlog import RunLog
from migration_review import scoring

ACCEPT = {"plan_id": "p-accept", "recommendation": "accept_as_is", "summary": "s", "stages": [
    {"id": "d", "kind": "deploy_new", "migration": "apply"},
    {"id": "r", "kind": "retire_old"}]}
STAGED = {"plan_id": "p-staged", "recommendation": "staged_rollout", "stages": [
    {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
    {"id": "d", "kind": "deploy_new", "migration": "state_only"},
    {"id": "r", "kind": "retire_old"},
    {"id": "c", "kind": "sql", "phase": "contract", "sql": ["COMMENT ON TABLE account_user IS 'fixture 2'"]}]}


class FakeRunner:
    """Stands in for staged.run: records one check per entry of `checks` and returns the
    runner's result shape, with the runner's own recommendation rule."""

    def __init__(self, checks=None, raises=None):
        self.checks = checks or {"case.old_write_new_read": True}
        self.raises = raises
        self.calls = []

    def __call__(self, log: RunLog, plan, tests, provenance="scripted", keep_db=False,
                 scenario=None):
        self.calls.append({"plan": plan, "tests": tests, "provenance": provenance,
                           "scenario": scenario})
        if self.raises:
            raise self.raises
        for name, ok in self.checks.items():
            log.check(name, ok, provenance="runner")
        failed = [c for c in log.checks if c["status"] == "FAIL"]
        ok = not failed
        return {"ok": ok, "plan_id": plan["plan_id"], "completed_all_stages": True,
                "halted": None if ok else "checks failed", "stages": [],
                "failed_checks": [staged.feedback_view(c) for c in failed],
                "run_db": None, "scenario": scenario,
                "recommendation": staged.recommendation(ok, plan)}


@pytest.fixture
def runner(monkeypatch):
    def install(**kw):
        fake = FakeRunner(**kw)
        monkeypatch.setattr(staged, "run", fake)
        return fake
    return install


def _verify(tmp_path, scenario, answer, model="m"):
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return scoring.verify(scenario, text, model=model, runs_root=tmp_path)


# --- target rule -----------------------------------------------------------------------------

def test_target_rule_per_scenario():
    assert scoring.target("add-table") == {"verdict": "PASS", "recommendations": ["accept_as_is"]}
    assert scoring.target("drop-column") == {"verdict": "PASS",
                                             "recommendations": ["staged_rollout"]}
    assert scoring.target("unique-last-name") == {"verdict": "FAIL", "recommendations": ["reject"]}
    assert scoring.target("rename-note")["verdict"] == "PASS"
    for sid in scenarios.SCENARIOS:          # every scenario has a target
        assert scoring.target(sid)["verdict"] in ("PASS", "FAIL")


# --- extraction ------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    json.dumps(ACCEPT),
    "Here is the plan:\n```json\n" + json.dumps(ACCEPT, indent=1) + "\n```\nDone.",
    "<think>maybe {\"stages\": []}</think>\n" + json.dumps(ACCEPT),
    "Plan follows " + json.dumps(ACCEPT) + " end",
])
def test_extract_plan(text):
    assert scoring.extract_plan(text) == ACCEPT


@pytest.mark.parametrize("text", [None, "", "no json at all", "[1, 2]"])
def test_extract_plan_none(text):
    assert scoring.extract_plan(text) is None


# --- verify ----------------------------------------------------------------------------------

def test_pass_on_target_rewards_and_reports_run(tmp_path, runner):
    fake = runner()
    out = _verify(tmp_path, "add-table", ACCEPT)
    assert out["reward"] == 1.0 and out["status"] == "runner_pass_on_target"
    assert out["runner_verdict"] == "pass" and out["runner_recommendation"] == "accept_as_is"
    assert out["run_id"] and (tmp_path / out["run_id"] / "summary.json").is_file()
    call = fake.calls[0]
    assert call["scenario"] == "add-table" and call["provenance"] == "agent:m"
    assert "recommendation" not in call["plan"]       # agent-side field never reaches the runner
    assert call["tests"] is None                       # mandatory runner checks only


def test_run_record_has_no_reward_or_target(tmp_path, runner):
    runner()
    out = _verify(tmp_path, "add-table", ACCEPT)
    run = tmp_path / out["run_id"]
    summary = json.loads((run / "summary.json").read_text())
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    for record in [summary, *events]:
        assert not {"reward", "target"} & set(record), record
    assert summary["runner_verdict"] == "pass"


def test_runner_fail_returns_failed_check_names(tmp_path, runner):
    runner(checks={"case.old_write_new_read": False, "notes.preserved": True})
    out = _verify(tmp_path, "add-table", ACCEPT)
    assert out["reward"] == 0.0 and out["status"] == "runner_did_not_pass"
    assert out["failed_checks"] == ["case.old_write_new_read"]
    assert out["runner_recommendation"] == "reject" and out["mask_sample"] is False


def test_policy_recommendation_must_match_runner(tmp_path, runner):
    runner()
    out = _verify(tmp_path, "add-table", {**ACCEPT, "recommendation": "reject"})
    assert out["reward"] == 0.0
    assert out["status"] == "policy_recommendation_disagrees_with_runner"


def test_runner_recommendation_must_be_on_target(tmp_path, runner):
    runner()                         # a passing staged plan where only accept_as_is is listed
    staged_apply = {**STAGED, "stages": [
        {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
        {"id": "d", "kind": "deploy_new", "migration": "apply"},
        {"id": "r", "kind": "retire_old"}]}
    out = _verify(tmp_path, "add-table", staged_apply)
    assert out["reward"] == 0.0 and out["status"] == "runner_recommendation_off_target"


def test_staged_rollout_on_target(tmp_path, runner):
    runner()
    out = _verify(tmp_path, "drop-column", STAGED)
    assert out["reward"] == 1.0 and out["runner_recommendation"] == "staged_rollout"


def test_reject_confirmed_when_no_safe_plan(tmp_path, runner):
    runner(checks={"constraint.unique_last_name": False})
    plan = {**ACCEPT, "recommendation": "reject"}
    out = _verify(tmp_path, "unique-last-name", plan)
    assert out["reward"] == 1.0 and out["status"] == "reject_confirmed_by_runner"
    assert out["failed_checks"] == ["constraint.unique_last_name"]


def test_reject_needs_the_policy_to_reject(tmp_path, runner):
    runner(checks={"constraint.unique_last_name": False})
    out = _verify(tmp_path, "unique-last-name", ACCEPT)
    assert out["reward"] == 0.0 and out["status"] == "policy_did_not_reject"


def test_reject_scenario_runner_pass_is_not_rewarded(tmp_path, runner):
    runner()
    out = _verify(tmp_path, "unique-last-name", {**ACCEPT, "recommendation": "reject"})
    assert out["reward"] == 0.0


def test_rejecting_a_safe_scenario_is_not_rewarded(tmp_path, runner):
    runner(checks={"case.old_write_new_read": False})
    out = _verify(tmp_path, "add-table", {**ACCEPT, "recommendation": "reject"})
    assert out["reward"] == 0.0


def test_invalid_plan_never_reaches_runner(tmp_path, runner):
    fake = runner()
    bad = {**ACCEPT, "stages": [{"id": "d", "kind": "deploy_new", "migration": "apply",
                                 "variant": "state_only"},           # add-table has no such build
                                {"id": "r", "kind": "retire_old"}]}
    out = _verify(tmp_path, "add-table", bad)
    assert out["status"] == "invalid_plan" and out["reward"] == 0.0 and out["run_id"] is None
    assert "variant" in out["error"] and not fake.calls


def test_no_json_never_reaches_runner(tmp_path, runner):
    fake = runner()
    out = _verify(tmp_path, "add-table", "I would just deploy it.")
    assert out["status"] == "no_plan_json" and out["reward"] == 0.0 and not fake.calls


def test_policy_cannot_claim_provenance(tmp_path, runner):
    fake = runner()
    _verify(tmp_path, "add-table", {**ACCEPT, "provenance": "manual"})
    assert "provenance" not in fake.calls[0]["plan"]
    assert fake.calls[0]["provenance"] == "agent:m"


def test_runner_exception_is_masked_infrastructure(tmp_path, runner):
    runner(raises=RuntimeError("postgres down"))
    out = _verify(tmp_path, "add-table", ACCEPT)
    assert out["reward"] == 0.0 and out["mask_sample"] is True
    assert out["status"] == "runner_exception" and "postgres down" in out["error"]
    summary = json.loads((tmp_path / out["run_id"] / "summary.json").read_text())
    assert summary["verdict"] == "ERROR"


def test_busy_lock_is_masked(tmp_path, runner, monkeypatch):
    from migproto import lock

    runner()
    monkeypatch.setattr(lock, "execution_lock", _busy)
    out = _verify(tmp_path, "add-table", ACCEPT)
    assert out["mask_sample"] is True and "ExecutionBusy" in out["error"]


def _busy(*a, **k):
    from migproto import lock
    raise lock.ExecutionBusy(None)


def test_verify_cli_without_runner(tmp_path, monkeypatch, capsys):
    import io

    import verify_cli

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"scenario_id": "add-table", "output_text": "nothing here"})))
    assert verify_cli.main(["--keep-env"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "no_plan_json" and out["reward"] == 0.0


def test_gym_env_avoids_live_batch_resources():
    from migration_review import env

    assert env.RUNNER_ENV["MIGPROTO_PORT_OLD"] == "8081"
    assert env.RUNNER_ENV["MIGPROTO_PORT_NEW"] == "8082"
    assert env.RUNNER_ENV["MIGPROTO_CACHE_URL"].endswith("/8")
    assert env.RUNNER_ENV["MIGPROTO_LOCK_PATH"] == str(env.ROOT / "migproto.lock")
