"""Investigator, planner and tester roles. Each role proposes; the runner disposes.

A role's output is only ever parsed as JSON data. Model output is never executed as
Python; proposed SQL goes through plans.validate_plan and db.run_guarded.
"""

import json
import re
from pathlib import Path

from .. import checks, plans
from ..config import PROPOSALS, WORKTREES
from .llm import LLM

SEARCH_ROOTS = ["saleor/account", "saleor/graphql/account", "saleor/core/utils/anonymization.py"]
SEARCH_RE = re.compile(r"\bnote\b")
CONTEXT_LINES = 2


def gather_hits(worktree: Path = WORKTREES["old"]) -> list[dict]:
    """Deterministic search tool (runner side), no model involved."""
    hits = []
    for root in SEARCH_ROOTS:
        base = worktree / root
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            rel = str(path.relative_to(worktree))
            if "/migrations/" in rel:
                continue
            lines = path.read_text(errors="replace").splitlines()
            for i, line in enumerate(lines):
                if SEARCH_RE.search(line):
                    lo, hi = max(0, i - CONTEXT_LINES), min(len(lines), i + CONTEXT_LINES + 1)
                    hits.append({"ref": f"{rel}:{i + 1}", "line": line.strip(),
                                 "context": "\n".join(lines[lo:hi])})
    return hits


def score_investigator(proposal: dict, hits: list[dict], ground_truth: dict) -> dict:
    hit_refs = {h["ref"] for h in hits}
    findings = proposal.get("findings", []) if isinstance(proposal, dict) else []
    cited = [f.get("ref") for f in findings if isinstance(f, dict)]
    invalid = [r for r in cited if r not in hit_refs]
    predicted = {f.get("ref") for f in findings
                 if isinstance(f, dict) and f.get("needs_change") and f.get("ref") in hit_refs}
    truth = set(ground_truth["needs_change"])
    tp = predicted & truth
    return {
        "hits": len(hits),
        "cited": len(cited),
        "invalid_refs": invalid,
        "predicted_needs_change": sorted(predicted),
        "true_positives": len(tp),
        "false_positives": sorted(predicted - truth),
        "false_negatives": sorted(truth - predicted),
        "precision": round(len(tp) / len(predicted), 3) if predicted else 0.0,
        "recall": round(len(tp) / len(truth), 3) if truth else 0.0,
        "implicit_paths_reported": proposal.get("implicit_paths", []),
    }


SCENARIO = """\
Target: Saleor (Django + GraphQL e-commerce), PostgreSQL 15.
INTENTIONALLY INTRODUCED SCENARIO: the Django model field User.note (table account_user,
column "note", type text NULL) is being renamed to customer_note (column "customer_note").
The public GraphQL field name `note` stays the same.
"""

INVESTIGATOR_SYSTEM = """You are the Investigator in a database-migration review.
Classify search hits for application code affected by a column/attribute rename.
Reply with one JSON object only, no prose outside JSON."""

PLANNER_SYSTEM = """You are the Planner in a database-migration review. You propose a
zero-downtime migration and deployment sequence as JSON that a deterministic runner will
execute on a disposable local PostgreSQL database. Reply with one JSON object only."""

TESTER_SYSTEM = """You are the Tester in a database-migration review. You propose
compatibility checks for a deterministic runner and later summarize execution evidence.
You do not decide pass/fail; the runner does. Reply with one JSON object only."""

PLAN_CONTRACT = """\
Facts about the deployment (all true, all enforced by the runner):
- OLD app version: unmodified code. Its Django model only knows column "note". Django's
  save() issues INSERT/UPDATE statements listing every model column it knows, so old-app
  writes set "note" and never mention "customer_note".
- NEW app version: code is already written and cannot be changed by your plan. Its model only
  knows column "customer_note"; it never reads or writes "note". Its Django migration for this
  rename is state-only (it issues no SQL), so all schema changes must come from your plan.
- Between your `deploy_new` and `retire_old` stages BOTH versions serve traffic concurrently
  against the same database (reads and writes of the same rows, interleaved).
- The runner checks after each stage that both live versions read the value most recently
  written by either version, that no existing note is lost, and that the old column is gone at
  the end.

Plan JSON schema (unknown keys are rejected):
{
  "plan_id": "short-id",
  "summary": "one paragraph",
  "write_consistency": "how writes from BOTH versions stay consistent",
  "revision_notes": "only when revising: what changed and why",
  "stages": [
    {"id": "unique", "kind": "sql", "phase": "expand" | "backfill" | "contract",
     "description": "...", "sql": ["one SQL statement per string", "..."],
     "repeat_until_zero_rows": false},
    {"id": "unique", "kind": "deploy_new", "description": "..."},
    {"id": "unique", "kind": "retire_old", "description": "..."}
  ]
}
Rules: exactly one deploy_new and one retire_old stage; at least one expand and one contract
sql stage; stages run in the listed order; each sql string is executed as a single statement
(PL/pgSQL function bodies with $$ quoting are fine); `repeat_until_zero_rows` re-runs a stage
until every statement affects 0 rows (use it for batched backfills). Only plpgsql/sql
languages; no DROP TABLE, TRUNCATE, COPY, roles, extensions or database-level statements.
"""


def _llm_call(llm: LLM, system: str, user: str, transcript: list) -> dict:
    return llm.chat_json(system, user, transcript)


def investigate(source: str, llm: LLM | None, hits: list[dict], transcript: list) -> dict:
    if source == "scripted":
        return json.loads((PROPOSALS / "scripted" / "investigator.json").read_text())
    body = "\n\n".join(f"### {h['ref']}\n{h['context']}" for h in hits)
    user = (
        f"{SCENARIO}\nThe runner searched the OLD code for the word `note` (excluding "
        f"migrations) under {SEARCH_ROOTS}. For EVERY hit decide whether it must change when "
        "the Django model attribute is renamed (model field definition, ORM attribute access, "
        "field-name strings used for tracking or bulk_update, tests that access the attribute) "
        "or is unrelated (other models, enums, docstrings, GraphQL input/output names that keep "
        "the name `note`).\n"
        "Also list implicit code paths that could break WITHOUT containing the word `note` "
        "(for example generic code that maps input keys to model field names).\n\n"
        'Reply as {"findings": [{"ref": "path:line", "kind": "read|write|definition|test|'
        'unrelated", "needs_change": true|false, "reason": "..."}], '
        '"implicit_paths": ["..."]}\n\n'
        f"Search hits:\n{body}"
    )
    return _llm_call(llm, INVESTIGATOR_SYSTEM, user, transcript)


def plan(source: str, llm: LLM | None, investigation: dict, transcript: list,
         feedback: dict | None = None, previous: dict | None = None) -> dict:
    if source == "scripted":
        name = "plan_v2.json" if feedback else "plan_v1.json"
        return json.loads((PROPOSALS / "scripted" / name).read_text())
    affected = [f for f in investigation.get("findings", []) if f.get("needs_change")]
    user = (
        f"{SCENARIO}\n{PLAN_CONTRACT}\n"
        f"Investigator findings (affected app code, already handled by the new app version):\n"
        f"{json.dumps(affected, indent=1)[:4000]}\n\n"
    )
    if feedback:
        user += (
            "Your previous plan was EXECUTED and FAILED these deterministic checks. Revise the "
            "plan (one revision allowed). Explain the root cause in revision_notes.\n"
            f"Previous plan:\n{json.dumps(previous, indent=1)}\n\n"
            f"Execution feedback:\n{json.dumps(feedback, indent=1, ensure_ascii=False)[:8000]}\n"
        )
    else:
        user += "Propose the plan."
    return _llm_call(llm, PLANNER_SYSTEM, user, transcript)


def propose_tests(source: str, llm: LLM | None, plan_obj: dict, transcript: list) -> dict:
    if source == "scripted":
        return json.loads((PROPOSALS / "scripted" / "tester.json").read_text())
    catalogue = {k: v for k, v in checks.CATALOGUE.items()}
    user = (
        f"{SCENARIO}\n{PLAN_CONTRACT}\nPlan under test:\n{json.dumps(plan_obj, indent=1)}\n\n"
        "The runner ALWAYS runs this mandatory catalogue right after deploy_new (both versions "
        f"live):\n{json.dumps(catalogue, indent=1, ensure_ascii=False)}\n\n"
        "Propose additional interleaved cases that could expose a consistency bug in THIS plan. "
        "Each step: {\"app\": \"old\"|\"new\", \"op\": \"read\"|\"write\"|\"create\"|\"clear\", "
        "\"user\": \"label\", \"value\": \"string (write/create only)\"}. Labels map to fresh "
        "existing customers, or to the customer created by a `create` step with that label. "
        "Reads are compared by the runner against the last value written to that label.\n"
        f"At most {plans.MAX_CASES} cases with at most {plans.MAX_STEPS} steps each.\n"
        'Reply as {"rationale": "...", "checks": ["catalogue ids to emphasize"], '
        '"custom_cases": [{"id": "snake_case", "description": "...", "steps": [...]}]}'
    )
    return _llm_call(llm, TESTER_SYSTEM, user, transcript)


def report_evidence(source: str, llm: LLM | None, result: dict, transcript: list) -> dict:
    failed = result.get("failed_checks", [])
    if source == "scripted":
        return {
            "summary": (f"Plan {result.get('plan_id')}: {len(failed)} failing checks"
                        + (": " + ", ".join(f["name"] for f in failed[:8]) if failed else "")),
            "suspected_cause": None,
        }
    user = (
        "Summarize this execution evidence for the planner in <=120 words and name the most "
        "likely root cause of any failure. You do not decide pass/fail.\n"
        f"{json.dumps(result, indent=1, ensure_ascii=False)[:8000]}\n"
        'Reply as {"summary": "...", "suspected_cause": "..."}'
    )
    return _llm_call(llm, TESTER_SYSTEM, user, transcript)
