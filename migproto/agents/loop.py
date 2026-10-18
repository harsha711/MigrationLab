"""Budgeted Investigator -> Planner -> Tester controller."""

import contextvars
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor

from .. import plans, runner_api
from ..config import PROPOSALS, WORKTREES
from ..runlog import SCRIPTED, RunLog, agent
from . import facts as scenario_facts
from . import roles, single, tracing
from .budget import BudgetExceeded, ReviewBudget
from .llm import LLM, LLMError
from .request import load_review_request, scenario_text

MAX_REVISIONS = 1
# Topology `parallel`: independent planners per round, each with its own isolated context.
PARALLEL_PLANNERS = 3


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


def _feedback(result: dict, tester_report: dict, failed_checks: list[dict],
              earlier: list[dict] | None = None) -> dict:
    """Planner feedback: runner facts first, then the tester's model-written diagnosis labelled
    as unverified, then a factual record of each earlier executed attempt. Run 220028 showed
    both failure modes this order avoids: a wrong tester diagnosis read ahead of the runner
    evidence that contradicted it, and a controller-picked "best attempt" (most passed checks)
    that pointed at a regression instead of the attempt that halted early but was closer."""
    out = _evidence(result, failed_checks)
    if tester_report is not None:
        out["tester_hypothesis"] = {
            "note": ("Model-written diagnosis of the runner evidence above. It is not verified; "
                     "where it disagrees with the runner evidence (expected, observed, schema, "
                     "step_id), the runner evidence is right."),
            "report": tester_report,
        }
    if earlier:
        out["earlier_attempts"] = {
            "note": ("Runner facts for each earlier executed attempt (your latest plan is "
                     "'Previous plan'). An attempt that halted early ran fewer checks, so its "
                     "failure count is not comparable; you may revise from any of these plans."),
            "attempts": earlier,
        }
    return _fit(out)


def _attempt_record(attempt: int, plan_obj: dict, result: dict,
                    failed_checks: list[dict]) -> dict:
    return {
        "attempt": attempt,
        "halted": result.get("halted"),
        "halted_step": result.get("halted_step"),
        "stages_run": [s.get("stage") for s in result.get("stages") or []],
        "failed_checks": [c["name"] for c in failed_checks],
        "plan": plan_obj,
    }


def _fit(feedback: dict, limit: int | None = None) -> dict:
    """Keep the feedback under the planner prompt's evidence cap by dropping the bulkiest
    optional parts first (earlier plans, oldest first; then earlier attempts), so the prompt
    slice never cuts the JSON at an arbitrary point."""
    limit = roles.EVIDENCE_CHARS if limit is None else limit

    def size() -> int:
        return len(json.dumps(feedback, indent=1, ensure_ascii=False))

    attempts = (feedback.get("earlier_attempts") or {}).get("attempts") or []
    for rec in attempts:
        if size() <= limit:
            break
        if "plan" in rec:
            rec["plan"] = "(omitted to fit the prompt)"
    while attempts and size() > limit:
        attempts.pop(0)
    if "earlier_attempts" in feedback and not attempts:
        del feedback["earlier_attempts"]
    failed = feedback.get("failed_checks") or []
    dropped = 0
    while failed and size() > limit:
        failed.pop()
        dropped += 1
        feedback["failed_checks_omitted"] = (
            f"{dropped} more failed checks omitted to fit the prompt; see outcome_counts")
    return feedback


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


def _log_human_feedback(log: RunLog, review: dict) -> None:
    """Human feedback is input to agents, never a runner observation or a verdict."""
    for index, item in enumerate(review.get("human_feedback", [])):
        # The item's own "kind" (issue/suggestion/test) would collide with the event kind.
        log.event("human_feedback", provenance="human", index=index,
                  feedback_kind=item["kind"], text=item["text"], author=item["author"],
                  at=item["at"])


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
            "parallel_planners": PARALLEL_PLANNERS if topology == "parallel" else None,
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
        _log_human_feedback(log, review)

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

        def call(role: str, fn, *args, client: LLM | None = None, **kwargs):
            client = client or llm
            if client:
                client.trace_name = role
            transcript_role = "single" if memory is not None else role
            tx = transcripts.setdefault(transcript_role, [])
            compaction = None
            if memory is not None:
                history, compaction = memory.begin()
                kwargs.update(history=history, system=single.SYSTEM)
            first = len(tx)
            try:
                out = fn(source, client, *args, tx, **kwargs)
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

        if topology == "parallel":
            last_result, reason = _run_parallel(
                log, prov, solution_origin, call, llm, investigation, plan_kwargs, test_kwargs,
                sfacts, scenario_id, budget, attempts)
            final_ok = bool(last_result and last_result["ok"])
            return _finish(final_ok, "PASS" if final_ok else "FAIL", source, topology, model,
                           score, attempts, budget, reason=reason, scenario=scenario_id)

        plan_obj = _tag(call("planner-1", roles.plan, investigation, **plan_kwargs), 1)
        _validate_plan(plan_obj, sfacts, log, prov, 1)
        log.event("proposal", provenance=prov, role="planner", attempt=1,
                  plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                  parent_plan_id=None, recommendation=plan_obj.get("recommendation"))

        earlier: list[dict] = []   # runner facts of executed attempts before the latest
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
            feedback = _feedback(result, report, complete_failures, [dict(r) for r in earlier])
            earlier.append(_attempt_record(attempt, plan_obj, result, complete_failures))
            try:
                plan_obj = _tag(call(
                    f"planner-{attempt + 1}", roles.plan, investigation,
                    feedback=feedback,
                    previous=plan_obj,
                    **plan_kwargs,
                ), attempt + 1)
                _validate_plan(plan_obj, sfacts, log, prov, attempt + 1)
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


def _parallel(jobs: list) -> list[tuple]:
    """Run zero-argument callables at once (vLLM batches the requests); returns one
    (value, exception) pair per job, in order. Each thread gets a copy of the caller's context
    so tracing spans stay under the review's trace."""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, job) for job in jobs]
        out = []
        for future in futures:
            try:
                out.append((future.result(), None))
            except Exception as exc:  # noqa: BLE001 - sorted by the caller
                out.append((None, exc))
    for _, exc in out:
        if isinstance(exc, BudgetExceeded):
            raise exc
    return out


def _run_parallel(log: RunLog, prov: str, solution_origin: str, call, llm: LLM | None,
                  investigation: dict, plan_kwargs: dict, test_kwargs: dict, sfacts,
                  scenario_id: str, budget: ReviewBudget,
                  attempts: list[dict]) -> tuple[dict | None, str | None]:
    """Topology `parallel` (decision D5): PARALLEL_PLANNERS planners per round, each an
    isolated role call with its own lineage. Every valid plan gets its own tester proposal
    and is executed by the runner, one at a time; the first runner PASS ends the review. A
    revision sees its own runner evidence plus the runner facts and plans of the other
    planners' attempts in the same round, never their model-written diagnosis (there is no
    tester report: on Lightning a wrong tester diagnosis misled revisions, D3). Shares the
    review budget with every other topology. Returns (last runner result, FAIL reason)."""
    k = PARALLEL_PLANNERS
    labels = "abcdefgh"[:k]
    # One client per planner (same endpoint and shared budget) so each thread names its own
    # role in traces.
    clients = [copy.copy(llm) if llm else None for _ in range(k)]
    lineages = [{"plan": None, "feedback": None, "earlier": []} for _ in range(k)]
    log.event("parallel_planners", provenance=prov, planners=k,
              msg=f"{k} planners per round; the runner executes every valid plan")
    attempt_no = 0
    last_result = None
    for rnd in range(1, budget.max_revisions + 2):
        def plan_job(i: int):
            lin = lineages[i]
            extra = ({} if lin["plan"] is None
                     else {"feedback": lin["feedback"], "previous": lin["plan"]})
            return lambda: call(f"planner-{rnd}{labels[i]}", roles.plan, investigation,
                                client=clients[i], **extra, **plan_kwargs)

        candidates = []
        for i, (obj, exc) in enumerate(_parallel([plan_job(i) for i in range(k)])):
            if exc is None:
                candidates.append((i, obj))
            elif isinstance(exc, (LLMError, plans.PlanError)):
                log.event("candidate_invalid", provenance=prov, round=rnd, planner=labels[i],
                          error=str(exc)[:500])
            else:
                raise exc
        if not candidates:
            if rnd == 1:
                raise LLMError("no parallel planner produced a valid plan")
            log.event("revision_invalid", provenance=prov, round=rnd,
                      error="no parallel planner produced a valid revision")
            return last_result, "revision invalid: no parallel planner produced a valid revision"

        def test_job(i: int, obj: dict):
            return lambda: call(f"tester-propose-{rnd}{labels[i]}", roles.propose_tests, obj,
                                client=clients[i], **test_kwargs)

        proposals = _parallel([test_job(i, obj) for i, obj in candidates])
        executed = []
        for (i, plan_obj), (tests, exc) in zip(candidates, proposals):
            attempt_no += 1
            lin = lineages[i]
            parent = lin["plan"]["plan_id"] if lin["plan"] else None
            plan_obj = _tag(plan_obj, attempt_no)
            _validate_plan(plan_obj, sfacts, log, prov, attempt_no)
            log.event("proposal", provenance=prov, role="planner", attempt=attempt_no,
                      plan_id=plan_obj["plan_id"], solution_origin=solution_origin,
                      parent_plan_id=parent, recommendation=plan_obj.get("recommendation"),
                      round=rnd, planner=labels[i])
            if exc is not None:
                if not isinstance(exc, (LLMError, plans.PlanError)):
                    raise exc
                # The mandatory runner checks still run; only the extra cases are missing.
                log.event("tester_invalid", provenance=prov, attempt=attempt_no,
                          error=str(exc)[:500])
                tests = None
            else:
                plans.validate_tests(tests)
                if isinstance(tests, dict):
                    tests["provenance"] = prov
                log.event("proposal", provenance=prov, role="tester", attempt=attempt_no,
                          plan_id=plan_obj["plan_id"], solution_origin=solution_origin)
            log.event("attempt", provenance=prov, name=f"attempt {attempt_no}",
                      plan_id=plan_obj.get("plan_id"), round=rnd, planner=labels[i])
            check_start = len(log.checks)
            result = runner_api.execute_plan(
                scenario_facts.runner_plan(plan_obj),
                run_context=runner_api.RunContext(log, provenance=prov, attempt=attempt_no),
                extra_tests=tests, scenario=scenario_id).as_dict()
            last_result = result
            failures = _failure_records(log, check_start)
            attempts.append({
                "attempt": attempt_no,
                "round": rnd,
                "planner": labels[i],
                "plan_id": result.get("plan_id"),
                "ok": result["ok"],
                "halted": result.get("halted"),
                "failed_checks": [c["name"] for c in failures],
                "tester_summary": None,
                "planner_recommendation": plan_obj.get("recommendation"),
                "runner_recommendation": result.get("recommendation"),
            })
            tracing.attempt(attempt_no, result, attempts[-1]["failed_checks"])
            log.event("attempt_result", provenance="runner", name=f"attempt {attempt_no}",
                      status="PASS" if result["ok"] else "FAIL",
                      failed=attempts[-1]["failed_checks"], round=rnd, planner=labels[i])
            if result["ok"]:
                return result, None
            executed.append((i, attempt_no, plan_obj, result, failures))
        if rnd > budget.max_revisions:
            break
        if all(p.get("recommendation") == "reject" and r.get("recommendation") == "reject"
               for _, _, p, r, _ in executed):
            log.event("revision_skipped", provenance=prov, round=rnd,
                      msg="every planner recommended reject and the runner rejected each plan")
            break
        records = {i: _attempt_record(n, p, r, f) for i, n, p, r, f in executed}
        for i, n, plan_obj, result, failures in executed:
            lin = lineages[i]
            others = [dict(records[j], author="another planner, same round")
                      for j in records if j != i]
            lin["feedback"] = _feedback(result, None, failures,
                                        [dict(r) for r in lin["earlier"]] + others)
            lin["earlier"].append(records[i])
            lin["plan"] = plan_obj
        # A planner whose reply was invalid this round keeps its lineage and retries; one
        # that has no executed plan yet continues from another planner's.
        done = [i for i, *_ in executed]
        for n, lin in enumerate(lineages):
            if lin["plan"] is None:
                src = lineages[done[n % len(done)]]
                lin.update(plan=src["plan"], feedback=src["feedback"],
                           earlier=list(src["earlier"]))
    return last_result, None


def _validate_plan(plan_obj: dict, sfacts, log: RunLog | None = None,
                   prov: str | None = None, attempt: int | None = None) -> None:
    try:
        scenario_facts.plan_validator(sfacts)(plan_obj)
    except scenario_facts.PreCheckError as exc:
        # chat_json already let this plan through after its last retry; record it and let
        # the runner judge the plan.
        if log is not None:
            log.event("precheck_not_met", provenance=prov, attempt=attempt,
                      plan_id=plan_obj.get("plan_id"), msg=str(exc)[:500])


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
