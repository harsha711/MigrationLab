"""Gym verification: the policy's answer -> plan -> deterministic runner -> reward.

The runner (migproto.runner_api.execute_plan, which runs migproto.staged.run under the
execution lock) is the only source of pass/fail. This module only maps the runner's verdict
and recommendation onto a reward, using the manual expected verdicts in
proposals/manual/scenarios/ (evaluation side only; never in a task, a prompt or a run record).

Reward 1.0 when
- a safe plan exists for the scenario (some manual plan is a runner PASS): the runner PASSes
  the policy's plan, the runner's recommendation is one of the PASS recommendations listed for
  the scenario, and the policy's own recommendation equals the runner's; or
- no safe plan exists (every manual plan is rejected): the policy recommends "reject" and the
  runner executed its (schema-valid) plan and failed or refused it (runner recommendation
  "reject"). A runner execution error does not count as a confirmed rejection.
Otherwise 0.0. This is the target() rule of scripts/brev/score_scenarios.py (branch
brev/lightning-serving), reimplemented here; rename-note has no manual label file and, as in
that script, any runner PASS of it is on target.

Infrastructure failures (the runner raised, e.g. the lock is busy or Postgres is down) return
reward 0.0 with mask_sample=True, so they are not counted as policy failures.
"""

import json
import re
import traceback
from pathlib import Path

from migproto import plans, runner_api, scenarios
from migproto.agents import facts, roles
from migproto.config import PROPOSALS
from migproto.runlog import RUNNER, RunLog, agent

EXPECTED = PROPOSALS / "manual" / "scenarios"
COMMAND = "gym-verify"
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def target(scenario_id: str, expected_dir: Path = EXPECTED) -> dict:
    """Acceptable (runner verdict, recommendations) for a scenario, from the manual labels."""
    scenarios.get(scenario_id)
    path = expected_dir / f"{scenario_id}.json"
    if scenario_id == scenarios.DEFAULT and not path.exists():
        return {"verdict": "PASS", "recommendations": ["accept_as_is", "staged_rollout"]}
    labels = json.loads(path.read_text())["plans"]
    passing = sorted({v["recommendation"] for v in labels.values() if v["verdict"] == "PASS"})
    if passing:
        return {"verdict": "PASS", "recommendations": passing}
    return {"verdict": "FAIL", "recommendations": ["reject"]}


def extract_plan(text: str | None) -> dict | None:
    """The plan object in a model answer: the whole answer, else the last fenced block, else
    the last top-level JSON object with "stages". Reasoning before </think> is ignored."""
    if not text:
        return None
    text = text.rsplit("</think>", 1)[-1].strip()
    candidates = [text] + list(reversed(_FENCE.findall(text)))
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    decoder, found, i = json.JSONDecoder(), None, text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "stages" in obj:
            found = obj
        i = text.find("{", end)
    return found


def _base(scenario_id: str) -> dict:
    return {"reward": 0.0, "scenario_id": scenario_id, "status": None, "mask_sample": False,
            "policy_recommendation": None, "plan_id": None, "run_id": None,
            "attempt_id": None, "runner_verdict": None, "runner_recommendation": None,
            "failed_checks": [], "halted": None, "error": None}


def _on_target(tgt: dict, policy_rec, report) -> tuple[bool, str]:
    if tgt["verdict"] == "PASS":
        if report.verdict != "pass":
            return False, "runner_did_not_pass"
        if report.recommendation not in tgt["recommendations"]:
            return False, "runner_recommendation_off_target"
        if policy_rec != report.recommendation:
            return False, "policy_recommendation_disagrees_with_runner"
        return True, "runner_pass_on_target"
    if policy_rec != "reject":
        return False, ("runner_pass_but_no_safe_plan_expected" if report.verdict == "pass"
                       else "policy_did_not_reject")
    if report.verdict in ("fail", "refused") and report.recommendation == "reject":
        return True, "reject_confirmed_by_runner"
    return False, f"reject_not_confirmed_runner_{report.verdict}"


def verify(scenario_id: str, output_text: str | None, model: str | None = None,
           runs_root: Path | None = None) -> dict:
    """Score one policy answer for one scenario. Runs the runner unless the answer has no
    schema-valid plan."""
    out = _base(scenario_id)
    tgt = target(scenario_id)
    plan_obj = extract_plan(output_text)
    if plan_obj is None:
        return {**out, "status": "no_plan_json"}
    out["policy_recommendation"] = plan_obj.get("recommendation")
    out["plan_id"] = plan_obj.get("plan_id")
    try:
        facts.plan_validator(facts.for_scenario(scenario_id, roles.SEARCH_ROOTS))(plan_obj)
    except plans.PlanError as exc:
        return {**out, "status": "invalid_plan", "error": str(exc)[:500]}

    prov = agent(model or "gym-policy")
    log = RunLog(COMMAND, **({"root": runs_root} if runs_root else {}))
    out["run_id"] = log.run_id
    log.event("gym_task", provenance=RUNNER, scenario=scenario_id, policy_provenance=prov,
              msg=f"gym verification of a policy plan for scenario {scenario_id}")
    log.event("proposal", provenance=prov, role="policy", attempt=1,
              plan_id=plan_obj.get("plan_id"), solution_origin="generated",
              recommendation=out["policy_recommendation"])
    log.write_json("agents/policy-answer.json", {"output_text": output_text, "model": model})
    # A policy cannot claim its own provenance; the runner records `prov`.
    runner_plan = {k: v for k, v in facts.runner_plan(plan_obj).items() if k != "provenance"}
    try:
        report = runner_api.execute_plan(
            runner_plan,
            run_context=runner_api.RunContext(log, provenance=prov, attempt=1),
            scenario=scenario_id)
    except Exception as exc:   # infrastructure: lock busy, services down, runner crash
        log.write("error.txt", traceback.format_exc())
        log.summary("ERROR", error=str(exc), scenario=scenario_id, source="gym")
        return {**out, "status": "runner_exception", "mask_sample": True,
                "error": f"{type(exc).__name__}: {exc}"[:500]}

    # Run record: runner facts and the policy's claim only; no reward or expected verdict.
    log.summary("PASS" if report.ok else ("ERROR" if report.verdict == "error" else "FAIL"),
                ok=report.ok, scenario=scenario_id, source="gym", provenance=prov,
                plan_id=report.plan_id, runner_verdict=report.verdict,
                runner_recommendation=report.recommendation,
                recommendation=out["policy_recommendation"], halted=report.halted)
    ok, status = _on_target(tgt, out["policy_recommendation"], report)
    return {**out, "reward": 1.0 if ok else 0.0, "status": status,
            "plan_id": report.plan_id, "attempt_id": report.attempt_id,
            "runner_verdict": report.verdict, "runner_recommendation": report.recommendation,
            "failed_checks": [c.get("name") for c in report.failed_checks],
            "halted": report.halted, "error": report.error}
