"""Budgeted Investigator -> Planner -> Tester controller."""

import json
import re

from .. import plans, runner_api
from ..config import PROPOSALS, WORKTREES
from ..runlog import SCRIPTED, RunLog, agent
from . import facts as scenario_facts
from . import roles, single, tracing
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


# Fields of a runner check record that a model needs to diagnose it. Full records (schema
# hashes, snapshot ids, provenance, timestamps) are ~1.2k chars each; a 13-failure attempt
# used to be cut to 4 visible failures, and the tester summary was cut off entirely.
_CHECK_FIELDS = ("name", "observation_id", "outcome", "app_version", "step_id", "error",
                 "expected", "observed")


def _compact_check(check: dict) -> dict:
    out = {k: check[k] for k in _CHECK_FIELDS if check.get(k) not in (None, "", [], {})}
    schema = check.get("schema_state") or {}
    if "note_columns" in schema or "triggers" in schema:
        out["schema"] = {k: schema.get(k) for k in ("note_columns", "triggers")}
    return out


def _evidence(result: dict, failed_checks: list[dict]) -> dict:
    """Runner evidence for model prompts: every failed check, compacted."""
    return {
        "ok": result.get("ok"),
        "plan_id": result.get("plan_id"),
        "halted": result.get("halted"),
        "error": result.get("error"),
        "outcome_counts": result.get("outcome_counts"),
        "stages": result.get("stages"),
        "failed_checks": [_compact_check(c) for c in failed_checks],
    }


def _feedback(result: dict, tester_report: dict, failed_checks: list[dict]) -> dict:
    # Tester summary first, so a prompt-length cap can never drop it.
    return {"tester_summary": tester_report, **_evidence(result, failed_checks)}


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


def run(log: RunLog, source: str, request=None, topology: str | None = None,
        scenario: str | None = None) -> dict:
    """Backward-compatible entry point used by the CLI."""
    return run_review(log, source, request=request, topology=topology, scenario=scenario)


def run_review(log: RunLog, source: str, request=None, topology: str | None = None,
               llm: LLM | None = None, scenario: str | None = None) -> dict:
    """Run one review; safe for future CLI and UI callers to share."""
    with tracing.review(log, source) as trace:
        try:
            result = _run_review(log, source, request=request, topology=topology, llm=llm,
                                 scenario=scenario, trace=trace)
        except BaseException as exc:
            tracing.fail(trace, exc)
            raise
        tracing.finish(trace, result)
        return result


def _run_review(log: RunLog, source: str, request=None, topology: str | None = None,
                llm: LLM | None = None, scenario: str | None = None,
                trace: tracing.Review | None = None) -> dict:
    budget = llm.budget if llm else ReviewBudget()
    if source not in {"scripted", "llm"}:
        return _finish(False, "ERROR", source, topology or "roles", None, None, [], budget,
                       reason="source must be 'scripted' or 'llm'")
    try:
        review = load_review_request(request, source, topology, scenario)
        topology = review["agent_topology"]
        scenario_id = review["scenario"]
        sfacts = scenario_facts.for_scenario(scenario_id, roles.SEARCH_ROOTS)
        if source == "scripted" and not sfacts.is_default:
            raise ValueError(f"scenario {scenario_id}: scripted agent proposals exist only "
                             "for rename-note; run a scenario's scripted plans with "
                             "`migproto staged --scenario`")
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
            "scenario": scenario_id,
            "history_compaction": single.POLICY if topology == "single" and llm else None,
            **(llm.serving_metadata() if llm else {}),
        }
        log.event("agent_config", provenance=prov, **config)
        tracing.configure(trace, topology=topology, model=model,
                          thinking_budget=getattr(llm, "thinking_budget", None),
                          reasoning_enabled=llm.reasoning_enabled if llm else None,
                          max_revisions=budget.max_revisions,
                          request_id=review.get("request_id"),
                          history_compaction=config["history_compaction"])
        log.write_json("agents/review-request.json", review)

        # The scenario's OLD app version is the code under review (deployed before the
        # change); never a new-version or state-only build.
        old_version = scenario_facts.old_version(scenario_id)
        if sfacts.is_default:
            hits = roles.gather_hits()
        else:
            hits = roles.gather_hits(WORKTREES[old_version],
                                     pattern=scenario_facts.search_regex(sfacts.search_terms))
        log.write_json("agents/search_hits.json", hits)
        terms = ", ".join(f"`{t}`" for t in sfacts.search_terms)
        log.event("tool", name="gather_hits", hits=len(hits), terms=list(sfacts.search_terms),
                  app_version=old_version,
                  msg=f"deterministic search for {terms} in old worktree {old_version!r} "
                      "(runner tool)")
        # Every scenario: agent-side validator (tolerates `recommendation`) and a schema limited
        # to the scenario's builds. Non-default scenarios also get their own facts; rename-note
        # keeps its original prompt text.
        plan_kwargs = {"validator": scenario_facts.plan_validator(sfacts),
                       "schema": roles.plan_schema(sfacts.variants)}
        test_kwargs = {}
        if not sfacts.is_default:
            plan_kwargs["contract"] = sfacts.contract
            test_kwargs.update(contract=sfacts.contract, cases=sfacts.cases,
                               notes=sfacts.tester_notes)

        transcripts: dict[str, list] = {}
        memory = single.Memory(single.SYSTEM) if topology == "single" and llm else None
        scenario = scenario_text(review)
        plan_kwargs["scenario"] = test_kwargs["scenario"] = scenario

        def call(role: str, fn, *args, **kwargs):
            if llm:
                llm.trace_name = role
            transcript_role = "single" if memory is not None else role
            tx = transcripts.setdefault(transcript_role, [])
            compaction = None
            if memory is not None:
                history, compaction = memory.begin()
                kwargs.update(history=history, system=single.SYSTEM)
            first = len(tx)
            try:
                out = fn(source, llm, *args, tx, **kwargs)
            finally:
                if memory is not None:
                    memory.end(role)
                    for record in tx[first:]:   # auditable: which turns this call replaced
                        record["history_compaction"] = compaction
                log.write_json(f"agents/transcript-{transcript_role}.json", tx)
            log.write_json(f"agents/{role}.json", out)
            return out

        investigation = call("investigator", roles.investigate, hits, scenario=scenario,
                             **({} if sfacts.is_default else
                                {"task": sfacts.investigator_task}))
        if sfacts.is_default:
            # Ground truth exists for rename-note only (manual label, never shown to agents).
            truth = json.loads(
                (PROPOSALS / "manual" / "investigator_ground_truth.json").read_text())
            score = roles.score_investigator(investigation, hits, truth)
            log.event("investigator_score", provenance=prov, msg=(
                f"precision={score['precision']} recall={score['recall']} vs manual ground "
                "truth"), **score)
        else:
            log.event("investigator_score", provenance=prov, skipped=True,
                      msg=f"no investigator ground truth for scenario {scenario_id}; not scored")

        plan_obj = _tag(call("planner-1", roles.plan, investigation, **plan_kwargs), 1)
        _validate_plan(plan_obj, sfacts)
        log.event("proposal", provenance=prov, role="planner", attempt=1,
                  plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                  parent_plan_id=None, recommendation=plan_obj.get("recommendation"))

        for attempt in range(1, budget.max_revisions + 2):
            tests = call(f"tester-propose-{attempt}", roles.propose_tests, plan_obj,
                         **test_kwargs)
            plans.validate_tests(tests)
            if isinstance(tests, dict):
                tests["provenance"] = prov
            log.event("proposal", provenance=prov, role="tester", attempt=attempt,
                      plan_id=plan_obj["plan_id"], solution_origin=solution_origin)
            log.event("attempt", provenance=prov, name=f"attempt {attempt}",
                      plan_id=plan_obj.get("plan_id"))
            check_start = len(log.checks)
            result = runner_api.execute_plan(
                scenario_facts.runner_plan(plan_obj),
                run_context=runner_api.RunContext(log, provenance=prov, attempt=attempt),
                extra_tests=tests, scenario=scenario_id).as_dict()
            last_result = result
            complete_failures = _failure_records(log, check_start)
            report = call(f"tester-report-{attempt}", roles.report_evidence,
                          _evidence(result, complete_failures))
            transcript_role = "single" if memory is not None else f"tester-report-{attempt}"
            last_call = transcripts.get(transcript_role, [])[-1:] or [None]
            _emit_claim(log, prov, attempt, report, complete_failures, last_call[0])
            attempts.append({
                "attempt": attempt,
                "plan_id": result.get("plan_id"),
                "ok": result["ok"],
                "halted": result.get("halted"),
                "failed_checks": [c["name"] for c in complete_failures],
                "tester_summary": report,
                "planner_recommendation": plan_obj.get("recommendation"),
                "runner_recommendation": result.get("recommendation"),
            })
            tracing.attempt(attempt, result, attempts[-1]["failed_checks"])
            if memory is not None:
                memory.note_attempt(attempt, result.get("plan_id"), result["ok"],
                                    result.get("halted"), attempts[-1]["failed_checks"])
            log.event("attempt_result", provenance="runner", name=f"attempt {attempt}",
                      status="PASS" if result["ok"] else "FAIL",
                      failed=attempts[-1]["failed_checks"])
            if result["ok"] or attempt > budget.max_revisions:
                break
            if (plan_obj.get("recommendation") == "reject"
                    and result.get("recommendation") == "reject"):
                # The planner concluded no safe plan exists and the runner rejected the plan
                # it submitted as evidence: nothing for a revision to fix.
                log.event("revision_skipped", provenance=prov, name=f"attempt {attempt}",
                          msg="planner recommended reject and the runner rejected the plan")
                break
            previous_id = plan_obj["plan_id"]
            try:
                plan_obj = _tag(call(
                    f"planner-{attempt + 1}", roles.plan, investigation,
                    feedback=_feedback(result, report, complete_failures), previous=plan_obj,
                    **plan_kwargs,
                ), attempt + 1)
                _validate_plan(plan_obj, sfacts)
            except (LLMError, plans.PlanError) as exc:
                # The runner already failed every executed plan; an unusable revision does
                # not make that an infrastructure error. BudgetExceeded still means INCOMPLETE.
                log.event("revision_invalid", provenance=prov, attempt=attempt + 1,
                          parent_plan_id=previous_id, error=str(exc)[:500])
                return _finish(False, "FAIL", source, topology, model, score, attempts,
                               budget, reason=f"revision invalid: {exc}",
                               scenario=scenario_id)
            log.event("proposal", provenance=prov, role="planner", attempt=attempt + 1,
                      plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                      parent_plan_id=previous_id,
                      recommendation=plan_obj.get("recommendation"))
    except BudgetExceeded as exc:
        return _finish(False, "INCOMPLETE", source, topology, model, score, attempts, budget,
                       reason=str(exc), scenario=scenario_id)
    except LLMError as exc:
        return _finish(False, "ERROR", source, topology, model, score, attempts, budget,
                       reason=f"LLM call failed: {exc}", scenario=scenario_id)
    except (ValueError, plans.PlanError) as exc:
        return _finish(False, "ERROR", source, topology, model, score, attempts, budget,
                       reason=f"invalid controller input or proposal: {exc}",
                       scenario=scenario_id)

    final_ok = bool(last_result and last_result["ok"])
    return _finish(final_ok, "PASS" if final_ok else "FAIL", source, topology, model,
                   score, attempts, budget, scenario=scenario_id)


def _validate_plan(plan_obj: dict, sfacts) -> None:
    scenario_facts.plan_validator(sfacts)(plan_obj)


def _finish(ok: bool, verdict: str, source: str, topology: str, model: str | None,
            score: dict | None, attempts: list[dict], budget: ReviewBudget,
            reason: str | None = None, scenario: str | None = None) -> dict:
    last = attempts[-1] if attempts else {}
    planner_rec = last.get("planner_recommendation")
    runner_rec = last.get("runner_recommendation")
    return {
        "ok": ok,
        "verdict": verdict,
        "scenario": scenario,
        # Planner's conclusion (agent claim) next to the runner's reading of its result.
        "recommendation": planner_rec,
        "runner_recommendation": runner_rec,
        "recommendation_agrees": (planner_rec == runner_rec
                                  if planner_rec and runner_rec else None),
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
