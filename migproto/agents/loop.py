"""Investigator -> Planner -> Tester -> deterministic execution, with one plan revision."""

import json
import re

from .. import runner_api
from ..config import PROPOSALS
from ..runlog import SCRIPTED, RunLog, agent
from . import roles
from .llm import LLM, LLMError

MAX_REVISIONS = 1


def _tag(obj: dict, attempt: int) -> dict:
    if isinstance(obj, dict):
        raw = re.sub(r"^(a\d+-)+", "", str(obj.get("plan_id") or "plan"))
        obj["plan_id"] = f"a{attempt}-" + (re.sub(r"[^a-z0-9-]+", "-", raw.lower())[:24].strip("-")
                                           or "plan")
    return obj


def _feedback(result: dict, tester_report: dict) -> dict:
    return {
        "halted": result.get("halted"),
        "error": result.get("error"),
        "stages": result.get("stages"),
        "failed_checks": result.get("failed_checks", [])[:10],
        "tester_summary": tester_report,
    }


def run(log: RunLog, source: str) -> dict:
    llm = LLM() if source == "llm" else None
    prov = agent(llm.model) if llm else SCRIPTED
    log.event("agent_config", provenance=prov, source=source,
              base_url=llm.base_url if llm else None, model=llm.model if llm else None,
              max_revisions=MAX_REVISIONS)

    hits = roles.gather_hits()
    log.write_json("agents/search_hits.json", hits)
    log.event("tool", name="gather_hits", hits=len(hits),
              msg="deterministic search for `note` in old worktree (runner tool)")

    transcripts: dict[str, list] = {}

    def call(role: str, fn, *args, **kwargs):
        tx = transcripts.setdefault(role, [])
        try:
            out = fn(source, llm, *args, tx, **kwargs)
        finally:
            log.write_json(f"agents/transcript-{role}.json", tx)
        log.write_json(f"agents/{role}.json", out)
        return out

    try:
        investigation = call("investigator", roles.investigate, hits)
        truth = json.loads((PROPOSALS / "manual" / "investigator_ground_truth.json").read_text())
        score = roles.score_investigator(investigation, hits, truth)
        log.event("investigator_score", provenance=prov, msg=(
            f"precision={score['precision']} recall={score['recall']} vs manual ground truth"),
            **score)

        attempts = []
        plan_obj = _tag(call("planner-1", roles.plan, investigation), 1)
        for attempt in range(1, MAX_REVISIONS + 2):
            tests = call(f"tester-propose-{attempt}", roles.propose_tests, plan_obj)
            if isinstance(tests, dict):
                tests["provenance"] = prov
            log.event("attempt", provenance=prov, name=f"attempt {attempt}",
                      plan_id=plan_obj.get("plan_id") if isinstance(plan_obj, dict) else None)
            result = runner_api.execute_plan(
                plan_obj, run_context=runner_api.RunContext(log, provenance=prov),
                extra_tests=tests).as_dict()
            report = call(f"tester-report-{attempt}", roles.report_evidence, result)
            attempts.append({"attempt": attempt, "plan_id": result.get("plan_id"),
                             "ok": result["ok"], "halted": result.get("halted"),
                             "failed_checks": [c["name"] for c in result.get("failed_checks", [])],
                             "tester_summary": report})
            log.event("attempt_result", provenance="runner", name=f"attempt {attempt}",
                      status="PASS" if result["ok"] else "FAIL",
                      failed=attempts[-1]["failed_checks"])
            if result["ok"] or attempt > MAX_REVISIONS:
                break
            plan_obj = _tag(call(f"planner-{attempt + 1}", roles.plan, investigation,
                                 feedback=_feedback(result, report), previous=plan_obj),
                            attempt + 1)
    except LLMError as exc:
        return {"ok": False, "verdict": "ERROR", "error": f"LLM call failed: {exc}"}

    final_ok = attempts[-1]["ok"]
    return {
        "ok": final_ok,
        "source": source,
        "provenance": prov,
        "investigator": {k: score[k] for k in ("precision", "recall", "false_positives",
                                               "false_negatives", "invalid_refs")},
        "attempts": attempts,
        "revision_exercised": len(attempts) > 1,
        "revision_fixed_failures": len(attempts) > 1 and final_ok and not attempts[0]["ok"],
    }
