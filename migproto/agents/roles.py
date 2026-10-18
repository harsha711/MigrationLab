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
# Prompt-size cap; rename-note has 35 hits, a common column such as last_name has ~140.
MAX_HITS = 80


# Experiment annotations in scenario old builds (patches/<scenario>/old) explain the trap; they
# are answers, not code under review.
_ANNOTATION_RE = re.compile(r"#.*\b(EXPERIMENT|not upstream)\b")


def _annotation_lines(lines: list[str]) -> set[int]:
    """Indices of experiment comment blocks: a matching comment line and the comment lines
    that directly continue it."""
    masked, in_block = set(), False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _ANNOTATION_RE.search(line) and stripped.startswith("#"):
            in_block = True
        elif not (in_block and stripped.startswith("#")):
            in_block = False
        if in_block:
            masked.add(i)
    return masked


def gather_hits(worktree: Path = WORKTREES["old"], pattern: re.Pattern = SEARCH_RE,
                max_hits: int = MAX_HITS) -> list[dict]:
    """Deterministic search tool (runner side), no model involved. Searches one OLD app
    version: the pinned upstream code, or a scenario's own old build (the code under review);
    the caller never passes a new-version or state-only build. Experiment annotation comments
    are removed from hits and context."""
    hits = []
    for root in SEARCH_ROOTS:
        base = worktree / root
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            rel = str(path.relative_to(worktree))
            if "/migrations/" in rel:
                continue
            lines = path.read_text(errors="replace").splitlines()
            masked = _annotation_lines(lines)
            for i, line in enumerate(lines):
                if i not in masked and pattern.search(line):
                    lo, hi = max(0, i - CONTEXT_LINES), min(len(lines), i + CONTEXT_LINES + 1)
                    hits.append({"ref": f"{rel}:{i + 1}", "line": line.strip(),
                                 "context": "\n".join(lines[j] for j in range(lo, hi)
                                                      if j not in masked)})
                    if len(hits) >= max_hits:
                        return hits
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

# Safety cap on runner evidence in prompts; compact evidence normally fits well under it.
EVIDENCE_CHARS = 24000

DEPLOY_MIGRATIONS = sorted(plans.DEPLOY_MIGRATIONS)


def plan_schema(variants: tuple[str, ...] | None = None) -> dict:
    """Guided-decoding schema for a plan: exactly the stage shapes plans.validate_plan accepts.

    Three stage shapes so guided decoding cannot put `sql` (even an empty list) on a non-sql
    stage, or `migration` / `variant` anywhere but deploy_new. With `variants` (a scenario's
    deployable builds), `variant` is limited to them and `migration` becomes required when the
    scenario has no state_only build (an omitted migration means state_only).
    `recommendation` is an agent-side field; the controller removes it before the runner."""
    deploy = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"const": "deploy_new"},
            "description": {"type": "string"},
            "migration": {"enum": DEPLOY_MIGRATIONS},
            "variant": ({"enum": list(variants)} if variants
                        else {"type": "string", "minLength": 1}),
        },
        "required": ["id", "kind"],
        "additionalProperties": False,
    }
    if variants is not None:
        # `variant` names an alternative build only; apply/state_only are chosen by
        # `migration` (the runner prefers an explicit variant, so a mismatch would deploy
        # the wrong build).
        named = [v for v in variants if v not in DEPLOY_MIGRATIONS]
        if named:
            deploy["properties"]["variant"] = {"enum": named}
        else:
            del deploy["properties"]["variant"]
    if variants is not None and "state_only" not in variants:
        deploy["properties"]["migration"] = {"enum": ["apply"]}
        deploy["required"] = ["id", "kind", "migration"]
    return {
        "name": "migration_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "recommendation": {"enum": ["accept_as_is", "staged_rollout", "reject"]},
                "summary": {"type": "string"},
                "write_consistency": {"type": "string"},
                "revision_notes": {"type": "string"},
                "addressed_feedback": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "index": {"type": "integer", "minimum": 0},
                        "action": {"enum": ["adopted", "adapted", "declined"]},
                        "reason": {"type": "string"},
                    }, "required": ["index", "action", "reason"],
                     "additionalProperties": False},
                },
                "stages": {
                    "type": "array",
                    "items": {"anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {"const": "sql"},
                                "phase": {"enum": ["expand", "backfill", "contract"]},
                                "description": {"type": "string"},
                                "sql": {"type": "array", "items": {"type": "string"},
                                        "minItems": 1},
                                "repeat_until_zero_rows": {"type": "boolean"},
                            },
                            "required": ["id", "kind", "phase", "sql"],
                            "additionalProperties": False,
                        },
                        deploy,
                        {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {"enum": ["retire_old", "rollback_check"]},
                                "description": {"type": "string"},
                            },
                            "required": ["id", "kind"],
                            "additionalProperties": False,
                        },
                    ]},
                },
            },
            "required": ["plan_id", "stages"],
            "additionalProperties": False,
        },
    }


PLAN_SCHEMA = plan_schema()

TESTS_SCHEMA = {
    "name": "tester_proposal",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "checks": {"type": "array", "items": {"type": "string"}},
            "custom_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "app": {"enum": ["old", "new"]},
                                    "op": {"enum": ["read", "write", "create", "clear"]},
                                    "user": {"type": "string"},
                                    "value": {"type": ["string", "null"]},
                                },
                                "required": ["app", "op", "user"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["id", "steps"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["checks", "custom_cases"],
        "additionalProperties": False,
    },
}

PLAN_CONTRACT = """\
Facts about the deployment (all true, all enforced by the runner):
- OLD app version: unmodified code. Its Django model only knows column "note". Django's
  save() issues INSERT/UPDATE statements listing every model column it knows, so old-app
  writes set "note" and never mention "customer_note".
- NEW app version: code is already written and cannot be changed by your plan. Its model only
  knows column "customer_note"; it never reads or writes "note". Its Django migration for this
  rename is state-only (it issues no SQL), so all schema changes must come from your plan.
  Builds you can deploy in the deploy_new stage:
  - "state_only" (set "migration": "state_only"; also the default when omitted): the new
    version's Django migration only records Django state and issues no SQL; your plan's SQL
    stages own the whole schema change.
  - "apply" (set "migration": "apply"): deploy_new runs the new version's own Django migration
    (its real SQL) on the database, then starts the new version.
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
    {"id": "unique", "kind": "deploy_new", "description": "...",
     "migration": "state_only" | "apply"},
    {"id": "unique", "kind": "retire_old", "description": "..."}
  ]
}
Rules: exactly one deploy_new and one retire_old stage; at least one sql stage, and every sql
stage must change the schema or data (the runner does not evaluate query results, so a stage of
only SELECT, SET or SHOW is rejected; checks belong in tester cases); stages run in the listed
order; each sql string is executed as a single statement
(PL/pgSQL function bodies with $$ quoting are fine); each string is sent to PostgreSQL exactly
as written, so write `$$` and quotes literally, never backslash-escaped; `repeat_until_zero_rows`
re-runs a stage until every statement affects 0 rows, so each repeated statement must stop
matching rows once its work is done (use it for batched backfills). Only plpgsql/sql
languages; no DROP TABLE, TRUNCATE, COPY, roles, extensions or database-level statements.
"""


def _llm_call(llm: LLM, system: str, user: str, transcript: list,
              validator=None, schema: dict | None = None,
              history: list | None = None) -> dict:
    return llm.chat_json(system, user, transcript, validator=validator,
                         response_schema=schema, history=history)


def investigate(source: str, llm: LLM | None, hits: list[dict], transcript: list,
                scenario: str = SCENARIO, history: list | None = None,
                system: str = INVESTIGATOR_SYSTEM, task: str | None = None) -> dict:
    if source == "scripted":
        return json.loads((PROPOSALS / "scripted" / "investigator.json").read_text())
    body = "\n\n".join(f"### {h['ref']}\n{h['context']}" for h in hits)
    if task is not None:
        user = (
            f"{scenario}\n{task}"
            'Reply as {"findings": [{"ref": "path:line", "kind": "read|write|definition|test|'
            'unrelated", "needs_change": true|false, "reason": "..."}], '
            '"implicit_paths": ["..."]}\n\n'
            f"Search hits ({len(hits)}):\n{body}"
        )
        return _llm_call(llm, system, user, transcript, history=history)
    user = (
        f"{scenario}\nThe runner searched the OLD code for the word `note` (excluding "
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
    return _llm_call(llm, system, user, transcript, history=history)


def plan(source: str, llm: LLM | None, investigation: dict, transcript: list,
         feedback: dict | None = None, previous: dict | None = None,
         human_feedback: list[dict] | None = None,
         scenario: str = SCENARIO, history: list | None = None,
         system: str = PLANNER_SYSTEM, contract: str = PLAN_CONTRACT,
         validator=plans.validate_plan, schema: dict = PLAN_SCHEMA) -> dict:
    if source == "scripted":
        name = "plan_v2.json" if feedback else "plan_v1.json"
        return json.loads((PROPOSALS / "scripted" / name).read_text())
    affected = [f for f in investigation.get("findings", []) if f.get("needs_change")]
    user = (
        f"{scenario}\n{contract}\n"
        f"Investigator findings (affected app code, already handled by the new app version):\n"
        f"{json.dumps(affected, indent=1)[:4000]}\n\n"
    )
    if feedback:
        user += (
            "Your previous plan was EXECUTED and FAILED these deterministic checks. Revise the "
            "plan. Explain the root cause in revision_notes.\n"
            f"Previous plan:\n{json.dumps(previous, indent=1)}\n\n"
            f"Execution feedback:\n{json.dumps(feedback, indent=1, ensure_ascii=False)[:EVIDENCE_CHARS]}\n"
        )
        if human_feedback:
            user += (
                "\nReviewer feedback (human, unverified; the runner will check any change you make):\n"
                f"{json.dumps(human_feedback, indent=1, ensure_ascii=False)}\n"
                "Record how you handled each item in addressed_feedback.\n"
            )
    else:
        user += "Propose the plan."
    return _llm_call(llm, system, user, transcript, validator=validator,
                     schema=schema, history=history)


def propose_tests(source: str, llm: LLM | None, plan_obj: dict, transcript: list,
                  scenario: str = SCENARIO, history: list | None = None,
                  system: str = TESTER_SYSTEM, contract: str = PLAN_CONTRACT,
                  cases: dict | None = None, notes: str = "",
                  human_tests: list[dict] | None = None) -> dict:
    if source == "scripted":
        return json.loads((PROPOSALS / "scripted" / "tester.json").read_text())
    # The cases the runner runs for this scenario (rename-note: the original 8).
    catalogue = cases if cases is not None else {
        k: checks.CATALOGUE[k] for k in checks.MANDATORY_AFTER_DEPLOY}
    user = (
        f"{scenario}\n{contract}\nPlan under test:\n{json.dumps(plan_obj, indent=1)}\n\n"
        "The runner ALWAYS runs this mandatory catalogue right after deploy_new (both versions "
        f"live):\n{json.dumps(catalogue, indent=1, ensure_ascii=False)}\n\n"
        "Propose additional interleaved cases that could expose a consistency bug in THIS plan. "
        "Each step: {\"app\": \"old\"|\"new\", \"op\": \"read\"|\"write\"|\"create\"|\"clear\", "
        "\"user\": \"label\", \"value\": \"string (write/create only)\"}. Labels map to fresh "
        "existing customers, or to the customer created by a `create` step with that label. "
        "Reads are compared by the runner against the last value written to that label.\n"
        + (f"{notes}\n" if notes else "")
        + ("Reviewer-requested extra test proposals (human, unverified). Consider these in "
           "your additional cases; they do not replace the mandatory catalogue:\n"
           f"{json.dumps(human_tests, indent=1, ensure_ascii=False)}\n"
           if human_tests else "") +
        f"At most {plans.MAX_CASES} cases with at most {plans.MAX_STEPS} steps each.\n"
        'Reply as {"rationale": "...", "checks": ["catalogue ids to emphasize"], '
        '"custom_cases": [{"id": "snake_case", "description": "...", "steps": [...]}]}'
    )
    return _llm_call(llm, system, user, transcript, validator=plans.validate_tests,
                     schema=TESTS_SCHEMA, history=history)


def report_evidence(source: str, llm: LLM | None, result: dict, transcript: list,
                    history: list | None = None, system: str = TESTER_SYSTEM) -> dict:
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
        f"{json.dumps(result, indent=1, ensure_ascii=False)[:EVIDENCE_CHARS]}\n"
        'Reply as {"summary": "...", "suspected_cause": "..."}'
    )
    return _llm_call(llm, system, user, transcript, history=history)
