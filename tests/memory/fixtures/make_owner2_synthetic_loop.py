"""Regenerates tests/memory/fixtures/owner2-synthetic-loop (SYNTHETIC, shaped after Owner 2's loop.py).

Usage: python tests/memory/fixtures/make_owner2_synthetic_loop.py tests/memory/fixtures/owner2-synthetic-loop
"""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
(out / "agents").mkdir(parents=True, exist_ok=True)
R = "20261017-160000-agent-loop-llm"
PROV = "agent:nemotron-3.5-lightning"
events = []
clock = [0.0]


def ev(kind, provenance="runner", **data):
    clock[0] += 0.5
    events.append({"t": round(clock[0], 3), "kind": kind, "provenance": provenance, **data})
    return len(events) - 1


def check(name, ok, provenance="runner", **data):
    return ev("check", provenance, name=name, status="PASS" if ok else "FAIL", **data)


budget = {"max_revisions": 1, "max_model_calls": 10, "max_total_tokens": 40000,
          "max_wall_seconds": 600, "model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
          "total_tokens": 0, "model_seconds": 0.0, "valid_proposals": 0, "exhausted_reason": None,
          "wall_seconds": 0.0, "valid_proposal_rate": None,
          "remaining": {"model_calls": 10, "tokens": 40000, "wall_seconds": 600}}
ev("agent_config", PROV, source="llm", topology="roles", base_url="http://synthetic-gpu-host:8000/v1",
   model="nemotron-3.5-lightning", temperature=1.0, top_p=0.95, reasoning_enabled=True,
   force_nonempty_content=True, budget=budget, solution_origin="generated",
   revision="bee7596271d1495f6992ae224aefde4410e816b8", serving_backend="vllm",
   serving_version="0.27.1", serving_flags=None, hardware="1x H100 (synthetic)", driver=None,
   cuda=None, brev_instance=None, prefix_caching=True, reasoning_parser="nemotron_v3")
ev("tool", name="gather_hits", hits=35, msg="deterministic search in old worktree (runner tool)")
ev("investigator_score", PROV, msg="precision=1.0 recall=0.875 vs manual ground truth",
   precision=1.0, recall=0.875)
smoke = [{"app": "old", "op": "write", "user": "S", "value": "smoke", "user_pk": 1, "ok": True, "error": None},
         {"app": "old", "op": "read", "user": "S", "user_pk": 1, "ok": True, "value": "smoke", "error": None}]
cross = [{"app": "old", "op": "write", "user": "A", "value": "old wrote this", "user_pk": 2, "ok": True, "error": None},
         {"app": "new", "op": "read", "user": "A", "user_pk": 2, "ok": True, "value": "old wrote this", "error": None}]

ev("proposal", PROV, role="planner", attempt=1, plan_id="a1-rename-note-001",
   solution_origin="generated", parent_plan_id=None)
ev("proposal", PROV, role="tester", attempt=1, plan_id="a1-rename-note-001", solution_origin="generated")
ev("attempt", PROV, name="attempt 1", plan_id="a1-rename-note-001")
check("plan.schema_valid", True, PROV, plan_id="a1-rename-note-001")
check("case.S0.old_smoke", True, "manual", steps=smoke)
ev("stage", PROV, stage="expand", stage_kind="sql", phase="expand", description="Add customer_note and sync triggers.")
check("stage.expand.executes", True, PROV, batches=None)
ev("schema", stage="expand", note_columns=["note", "customer_note"], triggers=["trg_a", "trg_b"])
ev("stage", PROV, stage="deploy_new", stage_kind="deploy_new", phase=None, description="Deploy new app.")
check("case.old_write_new_read", True, "manual", steps=cross)
ev("stage", PROV, stage="contract", stage_kind="sql", phase="contract", description="Drop old column.")
gate = check("retirement_gate", False, msg="old app still running", old_alive=True)
ev("agent_claim", PROV, contract_version="migproto.contracts/v1",
   claim_id=f"{R}/a1/tester-report/0", role="tester", claim_kind="diagnosis",
   text="The contract stage ran while the old app was still alive, so the runner refused it.",
   evidence=[f"{R}/a1/retirement_gate#{gate}", f"{R}/a1/case.never_recorded#999"],
   model_call={"role": "tester-report-1", "seconds": 0.9, "usage": {"total_tokens": 629}})
ev("attempt_result", name="attempt 1", status="FAIL", failed=["retirement_gate"])
ev("proposal", PROV, role="planner", attempt=2, plan_id="a2-rename-note-002",
   solution_origin="generated", parent_plan_id="a1-rename-note-001")
ev("proposal", PROV, role="tester", attempt=2, plan_id="a2-rename-note-002", solution_origin="generated")
ev("attempt", PROV, name="attempt 2", plan_id="a2-rename-note-002")
check("plan.schema_valid", True, PROV, plan_id="a2-rename-note-002")
ev("stage", PROV, stage="expand", stage_kind="sql", phase="expand", description="Add customer_note and sync triggers.")
check("stage.expand.executes", True, PROV, batches=None)
ev("schema", stage="expand", note_columns=["note", "customer_note"], triggers=["trg_a", "trg_b"])
ev("stage", PROV, stage="deploy_new", stage_kind="deploy_new", phase=None, description="Deploy new app.")
check("case.old_write_new_read", True, "manual", steps=cross)
ev("stage", PROV, stage="retire_old", stage_kind="retire_old", phase=None, description="Retire old app.")
check("retire_old.old_unreachable", True, msg="old app process stopped and port closed")
ev("stage", PROV, stage="contract", stage_kind="sql", phase="contract", description="Drop old column.")
check("retirement_gate", True, msg="old app not running")
check("stage.contract.executes", True, PROV, batches=None)
ev("attempt_result", name="attempt 2", status="PASS", failed=[])
ev("summary", verdict="PASS", msg=f"PASS -> <repo>/runs/{R}")

(out / "events.jsonl").write_text(
    "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
checks = [e for e in events if e["kind"] == "check"]
totals = {**budget, "model_calls": 8, "prompt_tokens": 9000, "completion_tokens": 12000,
          "total_tokens": 21000, "model_seconds": 31.2, "valid_proposals": 8, "wall_seconds": 74.5,
          "valid_proposal_rate": 1.0,
          "remaining": {"model_calls": 2, "tokens": 19000, "wall_seconds": 525.5}}
summary = {
    "note": ("SYNTHETIC test fixture shaped after Owner 2's run_review output "
             "(feature/aavritti/small-models). Not runner output; no Lightning run happened."),
    "command": "agent-loop-llm", "verdict": "PASS", "elapsed_s": 74.5,
    "checks_total": len(checks), "checks_failed": ["retirement_gate"], "ok": True, "source": "llm",
    "topology": "roles", "model": "nemotron-3.5-lightning", "provenance": PROV,
    "attempts": [
        {"attempt": 1, "plan_id": "a1-rename-note-001", "ok": False,
         "halted": "contract stage contract refused: old app still running",
         "failed_checks": ["retirement_gate"]},
        {"attempt": 2, "plan_id": "a2-rename-note-002", "ok": True, "halted": None,
         "failed_checks": []}],
    "revision_exercised": True, "revision_fixed_failures": True, "agent_totals": totals,
    "reason": None, "run_id": R}
(out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def plan(pid, notes):
    contract = {"id": "contract", "kind": "sql", "phase": "contract",
                "sql": ["ALTER TABLE account_user DROP COLUMN note"]}
    retire = {"id": "retire_old", "kind": "retire_old"}
    tail = [contract, retire] if not notes else [retire, contract]
    return {"plan_id": pid, "summary": f"Synthetic plan {pid}.",
            "write_consistency": "Two triggers mirror writes.", "revision_notes": notes,
            "provenance": PROV,
            "stages": [{"id": "expand", "kind": "sql", "phase": "expand",
                        "sql": ["ALTER TABLE account_user ADD COLUMN customer_note text"]},
                       {"id": "deploy_new", "kind": "deploy_new"}, *tail]}


(out / "plan-a1-rename-note-001.json").write_text(
    json.dumps(plan("a1-rename-note-001", ""), indent=2), encoding="utf-8")
(out / "plan-a2-rename-note-002.json").write_text(json.dumps(plan(
    "a2-rename-note-002",
    "Moved retire_old before contract after the retirement gate refused attempt 1."), indent=2),
    encoding="utf-8")
agents = {
    "review-request": {
        "contract_version": "migproto.contracts/v1", "request_id": "default-user-note-rename",
        "target": {"app": "saleor", "commit": "0c0e2697cc72aa999f7786ae8aeda7665bd4285f"},
        "change": {"description": ("Rename Django field User.note (account_user.note, text NULL) "
                                   "to customer_note. Keep the public GraphQL field name note."),
                   "table": "account_user", "old_column": "note", "new_column": "customer_note",
                   "intentionally_introduced": True},
        "app_versions": {"old": "old", "new": "new-staged"}, "mode": "llm",
        "agent_topology": "roles",
        "budget": {"max_revisions": 1, "max_model_calls": 10, "max_total_tokens": 40000,
                   "max_wall_seconds": 600}},
    "tester-report-1": {"summary": "Free-text summary for attempt 1 (superseded by the structured claim).",
                        "suspected_cause": "Old app still running."},
    "tester-report-2": {"summary": "Attempt 2 completed all stages.",
                        "suspected_cause": "None (no failure detected)"},
    "planner-2": {"plan_id": "a2-rename-note-002", "write_consistency": "Two triggers mirror writes.",
                  "revision_notes": "Moved retire_old before contract."},
}
for name, body in agents.items():
    (out / "agents" / f"{name}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
print(len(events), "events,", len(checks), "checks, gate seq", gate)
