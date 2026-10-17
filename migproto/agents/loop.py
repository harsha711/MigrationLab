"""Budgeted Investigator -> Planner -> Tester controller."""

import json
import re

from .. import plans, runner_api
from ..config import PROPOSALS
from ..runlog import SCRIPTED, RunLog, agent
from . import roles, single
from .budget import BudgetExceeded, ReviewBudget
from .llm import LLM, LLMError
from .request import load_review_request, scenario_text

MAX_REVISIONS = 1


def _tag(obj: dict, attempt: int) -> dict:
    if isinstance(obj, dict):
        raw = re.sub(r"^(a\d+-)+", "", str(obj.get("plan_id") or "plan"))
        obj["plan_id"] = f"a{attempt}-" + (
            re.sub(r"[^a-z0-9-]+", "-", raw.lower())[:24].strip("-") or "plan"
        )
    return obj


def _feedback(result: dict, tester_report: dict, failed_checks: list[dict]) -> dict:
    return {
        "halted": result.get("halted"),
        "error": result.get("error"),
        "stages": result.get("stages"),
        "failed_checks": failed_checks[:10],
        "tester_summary": tester_report,
    }


def _failure_records(log: RunLog, start: int) -> list[dict]:
    return [dict(check) for check in log.checks[start:] if check["status"] == "FAIL"]


def _emit_claim(log: RunLog, prov: str, attempt: int, report: dict,
                failed_checks: list[dict], model_call: dict | None) -> None:
    if not failed_checks:
        return
    text = report.get("suspected_cause") or report.get("summary")
    if not text:
        return
    evidence = [c["observation_id"] for c in failed_checks if c.get("observation_id")]
    log.event(
        "agent_claim",
        provenance=prov,
        contract_version="migproto.contracts/v1",
        claim_id=f"{log.dir.name}/a{attempt}/tester-report/0",
        role="tester",
        claim_kind="diagnosis",
        text=text,
        evidence=evidence,
        model_call=model_call,
    )


def run(log: RunLog, source: str, request=None, topology: str | None = None) -> dict:
    """Backward-compatible entry point used by the CLI."""
    return run_review(log, source, request=request, topology=topology)


def run_review(log: RunLog, source: str, request=None, topology: str | None = None,
               llm: LLM | None = None) -> dict:
    """Run one review; safe for future CLI and UI callers to share."""
    budget = llm.budget if llm else ReviewBudget()
    if source not in {"scripted", "llm"}:
        return _finish(False, "ERROR", source, topology or "roles", None, None, [], budget,
                       reason="source must be 'scripted' or 'llm'")
    try:
        review = load_review_request(request, source, topology)
        topology = review["agent_topology"]
        if llm is None:
            budget = ReviewBudget.from_dict(review.get("budget"))
    except ValueError as exc:
        return _finish(False, "ERROR", source, topology or "roles", None, None, [], budget,
                       reason=f"invalid review request: {exc}")
    attempts: list[dict] = []
    score = None
    last_result = None
    model = None

    try:
        if source == "llm":
            llm = llm or LLM(budget=budget)
            model = llm.model
        prov = agent(model) if llm else SCRIPTED
        solution_origin = "generated" if llm else "scripted"
        config = {
            "source": source,
            "topology": topology,
            "base_url": llm.base_url if llm else None,
            "model": model,
            "temperature": llm.temperature if llm else None,
            "top_p": llm.top_p if llm else None,
            "reasoning_enabled": llm.reasoning_enabled if llm else None,
            "force_nonempty_content": llm.force_nonempty_content if llm else None,
            "budget": budget.snapshot(),
            "solution_origin": solution_origin,
            **(llm.serving_metadata() if llm else {}),
        }
        log.event("agent_config", provenance=prov, **config)
        log.write_json("agents/review-request.json", review)

        hits = roles.gather_hits()
        log.write_json("agents/search_hits.json", hits)
        log.event("tool", name="gather_hits", hits=len(hits),
                  msg="deterministic search for `note` in old worktree (runner tool)")

        transcripts: dict[str, list] = {}
        shared_history: list[dict] | None = [] if topology == "single" and llm else None
        scenario = scenario_text(review)

        def call(role: str, fn, *args, **kwargs):
            transcript_role = "single" if shared_history is not None else role
            tx = transcripts.setdefault(transcript_role, [])
            if shared_history is not None:
                kwargs.update(history=shared_history, system=single.SYSTEM)
            try:
                out = fn(source, llm, *args, tx, **kwargs)
            finally:
                log.write_json(f"agents/transcript-{transcript_role}.json", tx)
            log.write_json(f"agents/{role}.json", out)
            return out

        investigation = call("investigator", roles.investigate, hits, scenario=scenario)
        truth = json.loads((PROPOSALS / "manual" / "investigator_ground_truth.json").read_text())
        score = roles.score_investigator(investigation, hits, truth)
        log.event("investigator_score", provenance=prov, msg=(
            f"precision={score['precision']} recall={score['recall']} vs manual ground truth"),
            **score)

        plan_obj = _tag(call("planner-1", roles.plan, investigation, scenario=scenario), 1)
        plans.validate_plan(plan_obj)
        log.event("proposal", provenance=prov, role="planner", attempt=1,
                  plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                  parent_plan_id=None)

        for attempt in range(1, budget.max_revisions + 2):
            tests = call(f"tester-propose-{attempt}", roles.propose_tests, plan_obj,
                         scenario=scenario)
            plans.validate_tests(tests)
            if isinstance(tests, dict):
                tests["provenance"] = prov
            log.event("proposal", provenance=prov, role="tester", attempt=attempt,
                      plan_id=plan_obj["plan_id"], solution_origin=solution_origin)
            log.event("attempt", provenance=prov, name=f"attempt {attempt}",
                      plan_id=plan_obj.get("plan_id"))
            check_start = len(log.checks)
            result = runner_api.execute_plan(
                plan_obj, run_context=runner_api.RunContext(log, provenance=prov,
                                                            attempt=attempt),
                extra_tests=tests).as_dict()
            last_result = result
            complete_failures = _failure_records(log, check_start)
            report = call(f"tester-report-{attempt}", roles.report_evidence,
                          {**result, "failed_checks": complete_failures})
            transcript_role = "single" if shared_history is not None else f"tester-report-{attempt}"
            last_call = transcripts.get(transcript_role, [])[-1:] or [None]
            _emit_claim(log, prov, attempt, report, complete_failures, last_call[0])
            attempts.append({
                "attempt": attempt,
                "plan_id": result.get("plan_id"),
                "ok": result["ok"],
                "halted": result.get("halted"),
                "failed_checks": [c["name"] for c in complete_failures],
                "tester_summary": report,
            })
            log.event("attempt_result", provenance="runner", name=f"attempt {attempt}",
                      status="PASS" if result["ok"] else "FAIL",
                      failed=attempts[-1]["failed_checks"])
            if result["ok"] or attempt > budget.max_revisions:
                break
            previous_id = plan_obj["plan_id"]
            plan_obj = _tag(call(
                f"planner-{attempt + 1}", roles.plan, investigation,
                feedback=_feedback(result, report, complete_failures), previous=plan_obj,
                scenario=scenario,
            ), attempt + 1)
            plans.validate_plan(plan_obj)
            log.event("proposal", provenance=prov, role="planner", attempt=attempt + 1,
                      plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                      parent_plan_id=previous_id)
    except BudgetExceeded as exc:
        return _finish(False, "INCOMPLETE", source, topology, model, score, attempts, budget,
                       reason=str(exc))
    except LLMError as exc:
        return _finish(False, "ERROR", source, topology, model, score, attempts, budget,
                       reason=f"LLM call failed: {exc}")
    except (ValueError, plans.PlanError) as exc:
        return _finish(False, "ERROR", source, topology, model, score, attempts, budget,
                       reason=f"invalid controller input or proposal: {exc}")

    final_ok = bool(last_result and last_result["ok"])
    return _finish(final_ok, "PASS" if final_ok else "FAIL", source, topology, model,
                   score, attempts, budget)


def _finish(ok: bool, verdict: str, source: str, topology: str, model: str | None,
            score: dict | None, attempts: list[dict], budget: ReviewBudget,
            reason: str | None = None) -> dict:
    return {
        "ok": ok,
        "verdict": verdict,
        "source": source,
        "topology": topology,
        "model": model,
        "provenance": agent(model) if model else SCRIPTED,
        "investigator": ({k: score[k] for k in (
            "precision", "recall", "false_positives", "false_negatives", "invalid_refs"
        )} if score else None),
        "attempts": attempts,
        "revision_exercised": len(attempts) > 1,
        "revision_fixed_failures": len(attempts) > 1 and ok and not attempts[0]["ok"],
        "agent_totals": budget.snapshot(),
        "reason": reason,
    }
