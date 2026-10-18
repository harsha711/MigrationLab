"""Per-scenario prompt facts for the agent roles (agent side; the runner owns the checks).

Everything here is derived from the scenario registry (`migproto/scenarios.py`): the change
description, the variants the runner can deploy, and the runner's own check configuration.
Never read scripted plans (proposals/scripted/), expected verdicts (proposals/manual/),
patches/ or examples/ here: those are answers, and prompts must not contain them.

`rename-note` keeps its original, hand-written prompt text (roles.SCENARIO-era facts and
roles.PLAN_CONTRACT) so the live rename runs stay comparable.
"""

import json
import re
from dataclasses import dataclass

from .. import checks, plans, scenarios

RECOMMENDATIONS = ("accept_as_is", "staged_rollout", "reject")

VARIANT_TEXT = {
    "apply": ("deploy_new runs the new version's own Django migration (its real SQL) on the "
              "database, then starts the new version"),
    "state_only": ("the new version's Django migration only records Django state and issues "
                   "no SQL; your plan's SQL stages own the whole schema change"),
}


@dataclass(frozen=True)
class ScenarioFacts:
    scenario_id: str
    contract: str                  # planner/tester facts + plan schema + rules
    search_terms: tuple[str, ...]  # identifiers gather_hits looks for in the old code
    investigator_task: str | None  # None: keep roles' original rename wording
    cases: dict                    # mandatory cases the runner runs after deploy_new
    tester_notes: str              # scenario-specific limits on tester ops
    variants: tuple[str, ...]
    is_default: bool


def search_terms(sc: scenarios.Scenario) -> tuple[str, ...]:
    """Schema identifiers named by the change: the columns, attribute and table under review."""
    if sc.id == scenarios.DEFAULT:
        return ("note",)
    keys = ("old_column", "indexed_column", "new_column", "new_attribute", "new_table")
    terms = [sc.change[k] for k in keys if sc.change.get(k)]
    return tuple(dict.fromkeys(terms))


def search_regex(terms: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b")


def _variant_lines(sc: scenarios.Scenario) -> list[str]:
    lines = []
    for key in sc.variants:
        if key in VARIANT_TEXT:
            lines.append(f'  - "{key}" (set "migration": "{key}"): {VARIANT_TEXT[key]}.')
        else:
            lines.append(f'  - "{key}" (set "variant": "{key}" and "migration": "apply"): an '
                         "alternative build of the new version; its own Django migration runs "
                         "on deploy_new.")
    if "state_only" not in sc.variants:
        lines.append('  There is no "state_only" build for this change, so deploy_new must set '
                     '"migration": "apply" (an omitted migration means state_only).')
    return lines


def _check_lines(sc: scenarios.Scenario) -> list[str]:
    if sc.rename_hooks:
        first = ("- Before your first stage, after expand stages and after deploy_new, the "
                 "runner checks that every customer's note in column \"note\" still equals its "
                 "most recent value (seed notes plus every note written during the run), "
                 "whenever that column exists.")
    else:
        first = ("- Before your first stage, after every SQL stage and after deploy_new, the "
                 "runner checks that every customer's note in column \"note\" still equals its "
                 "most recent value (seed notes plus every note written during the run), "
                 "whenever that column exists.")
    lines = [
        first,
        "- deploy_new: `manage.py migrate` of the new version must exit 0; each live version "
        "must then pass a smoke check; then the runner runs the mandatory cross-version cases "
        "(both versions live, interleaved; a read must return the value most recently written "
        "by either version).",
    ]
    preserve = [c for c in sc.preserve if c != "note"]
    if sc.rename_hooks:
        old_col, new_col = sc.change["old_column"], sc.change["new_column"]
        lines += [
            f"- Column \"{new_col}\" must also hold every customer's current note after "
            "backfill stages, after deploy_new and after contract stages.",
            f"- When both \"{old_col}\" and \"{new_col}\" exist, every row must hold the same "
            "value in both (after expand stages while the new version is live, after backfill "
            "stages and after deploy_new). A backfill stage requires both columns to exist.",
            f"- After a contract stage, column \"{old_col}\" must be gone.",
        ]
    elif preserve:
        lines.append(f"- Columns {preserve} must also hold every customer's current note "
                     "whenever they exist.")
    if sc.not_null:
        phases = ", ".join("deploy_new" if p == "deploy" else f"{p} sql"
                           for p in sc.not_null_phases)
        lines.append(f"- Columns {list(sc.not_null)} must contain no NULL in any row "
                     f"(including rows the old version inserted), checked after these stages "
                     f"whenever the column exists: {phases}.")
    if sc.unchanged:
        lines.append(f"- Pre-existing rows' values in {list(sc.unchanged)} must not change "
                     "during the rollout.")
    if not sc.rename_hooks and sc.present_after_contract:
        lines.append(f"- After a contract stage, columns {list(sc.present_after_contract)} "
                     "must exist.")
    if not sc.rename_hooks and sc.absent_after_contract:
        lines.append(f"- After a contract stage, columns {list(sc.absent_after_contract)} "
                     "must be gone.")
    lines += [
        "- A contract SQL stage is refused while the old version is still running (place it "
        "after retire_old), and skipped if any earlier check failed.",
        "- An optional rollback_check stage (between deploy_new and retire_old) stops the new "
        "version, migrates the database back to the previous Django migration, checks the "
        "notes and the old version, then re-applies the migration and restarts the new "
        "version.",
    ]
    return lines


PLAN_SCHEMA_TEXT = """\
Plan JSON schema (unknown keys are rejected):
{
  "plan_id": "short-id",
  "recommendation": "accept_as_is" | "staged_rollout" | "reject",
  "summary": "one paragraph",
  "write_consistency": "how writes from BOTH versions stay correct during the overlap",
  "revision_notes": "only when revising: what changed and why",
  "stages": [
    {"id": "unique", "kind": "sql", "phase": "expand" | "backfill" | "contract",
     "description": "...", "sql": ["one SQL statement per string", "..."],
     "repeat_until_zero_rows": false},
    {"id": "unique", "kind": "deploy_new", "migration": "state_only" | "apply",
     "variant": "optional variant name", "description": "..."},
    {"id": "unique", "kind": "rollback_check", "description": "optional"},
    {"id": "unique", "kind": "retire_old", "description": "..."}
  ]
}
Recommendation:
- "accept_as_is": the change is safe to deploy directly: deploy_new with "migration": "apply",
  then retire_old, and no SQL stages.
- "staged_rollout": the change needs your SQL stages around the deploy (at least one sql
  stage). With "migration": "state_only" the plan needs at least one expand and one contract
  sql stage; with "apply" SQL stages are optional.
- "reject": you believe no plan keeps both versions correct. Still submit the plan you consider
  closest (for example the direct deploy) so the runner records evidence, and say why in
  summary.
Rules: exactly one deploy_new and one retire_old stage, at most one rollback_check; stages run
in the listed order; only sql stages carry "sql", only deploy_new carries "migration" or
"variant"; each sql string is executed as a single statement (PL/pgSQL function bodies with $$
quoting are fine); each string is sent to PostgreSQL exactly as written, so write `$$` and
quotes literally, never backslash-escaped; `repeat_until_zero_rows` re-runs a stage until
every statement affects 0 rows, so each repeated statement must stop matching rows once its
work is done (use it for batched backfills). Only plpgsql/sql languages; no DROP TABLE,
TRUNCATE, COPY, roles, extensions or database-level statements.
"""


def contract_text(sc: scenarios.Scenario) -> str:
    if sc.old_version == "old":
        old = ("- OLD app version: the unmodified pinned Saleor code; it keeps serving traffic "
               "until retire_old.")
    else:
        old = (f"- OLD app version: this scenario's own old build ({sc.old_version!r}), the "
               "currently deployed code; it keeps serving traffic until retire_old.")
    lines = [
        "Facts about the deployment (all true, all enforced by the runner):",
        old,
        "- Django's save() issues INSERT/UPDATE statements listing every model column the "
        "running version knows; a column its model does not know is never mentioned, so on "
        "INSERT it gets the column's database DEFAULT (or NULL).",
        "- NEW app version: the code implementing the change is already written and cannot be "
        "changed by your plan. Builds you can deploy in the deploy_new stage:",
        *_variant_lines(sc),
        "- Between your `deploy_new` and `retire_old` stages BOTH versions serve traffic "
        "concurrently against the same database (reads and writes of the same rows, "
        "interleaved). After retire_old the old version never serves traffic again.",
        ("- Both versions read and write customer notes through the GraphQL field `note`."
         if sc.new_app_note_ops else
         "- The OLD version reads and writes customer notes through the GraphQL field `note`; "
         "the NEW version has no `note` field."),
        "Runner checks (deterministic; you do not decide pass/fail):",
        *_check_lines(sc),
        "",
    ]
    return "\n".join(lines) + "\n" + PLAN_SCHEMA_TEXT


def investigator_task(sc: scenarios.Scenario, roots: list[str]) -> str:
    terms = ", ".join(f"`{t}`" for t in search_terms(sc))
    return (
        f"The runner searched the pinned upstream Saleor code for the identifiers {terms} "
        f"(whole words, excluding migrations) under {roots}. For EVERY hit decide whether "
        "application code at that line is affected by the change under review (model field "
        "definition, ORM attribute access, raw SQL on the column, field-name strings used "
        "for tracking or bulk_update, GraphQL inputs/outputs that read or write it, tests that "
        "access it) or is unrelated (other models or tables, enums, docstrings).\n"
        "Also list implicit code paths that could break WITHOUT containing these identifiers "
        "(for example generic code that maps input keys to model field names, or code that "
        "inserts rows into the table).\n"
        "If there are no hits, return an empty findings list and still list implicit paths.\n\n"
    )


def tester_notes(sc: scenarios.Scenario) -> str:
    notes = ("Every op acts on a customer's note through the GraphQL `note` field of that app "
             "version; no op reads or writes other columns.")
    if any(st.get("op") == "create_plain" for cid in sc.cases for st in checks.CATALOGUE[cid]):
        notes += (" `create_plain` (create a customer without a note) is runner-internal and "
                  "not available to your cases.")
    if not sc.new_app_note_ops:
        notes += (" The NEW version has no `note` field: steps with app \"new\" and op "
                  "read/write/clear are recorded as skipped, and a new-app create with a note "
                  "value fails, so test the old version's paths.")
    return notes


def for_scenario(scenario_id: str | None, roots: list[str]) -> ScenarioFacts:
    sc = scenarios.get(scenario_id)
    default = sc.id == scenarios.DEFAULT
    from . import roles  # roles imports this module lazily; avoid a cycle at import time

    return ScenarioFacts(
        scenario_id=sc.id,
        contract=roles.PLAN_CONTRACT if default else contract_text(sc),
        search_terms=search_terms(sc),
        investigator_task=None if default else investigator_task(sc, roots),
        cases={cid: checks.CATALOGUE[cid] for cid in dict.fromkeys(sc.cases)},
        tester_notes="" if default else tester_notes(sc),
        variants=tuple(sc.variants),
        is_default=default,
    )


def old_version(scenario_id: str | None) -> str:
    """Worktree key of the scenario's OLD app version: the code under review, deployed before
    the change. Refuses any key that is also a new-version (apply or state-only) build."""
    sc = scenarios.get(scenario_id)
    new_builds = {v.version for s in scenarios.SCENARIOS.values() for v in s.variants.values()}
    if sc.old_version in new_builds:
        raise ValueError(f"scenario {sc.id}: old version {sc.old_version!r} is a new-version "
                         "build; gather_hits searches only old code")
    return sc.old_version


def plan_validator(sc_facts: ScenarioFacts):
    """Agent-side plan check: plans.validate_plan on the plan without `recommendation`, plus
    the recommendation's consistency with the plan shape and the scenario's variants.
    Raises plans.PlanError (a ValueError), so chat_json asks the model to correct it."""

    def validate(obj: dict) -> dict:
        if not isinstance(obj, dict):
            raise plans.PlanError("plan must be a JSON object")
        rec = obj.get("recommendation")
        plans.validate_plan(runner_plan(obj))
        variant = plans.deploy_variant(obj)
        if variant not in sc_facts.variants:
            raise plans.PlanError(f"deploy_new variant {variant!r} does not exist for this "
                                  f"change; available: {list(sc_facts.variants)}")
        if rec is None:
            return obj
        if rec not in RECOMMENDATIONS:
            raise plans.PlanError(f"recommendation must be one of {list(RECOMMENDATIONS)}")
        has_sql = any(st.get("kind") == "sql" for st in obj["stages"])
        if rec == "accept_as_is" and (has_sql or plans.deploy_migration(obj) != "apply"):
            raise plans.PlanError('accept_as_is needs deploy_new "migration": "apply" and no '
                                  "sql stages")
        if rec == "staged_rollout" and not has_sql:
            raise plans.PlanError("staged_rollout needs at least one sql stage")
        return obj

    return validate


def runner_plan(plan_obj: dict) -> dict:
    """The plan as the runner accepts it: the agent-side `recommendation` removed."""
    return {k: v for k, v in plan_obj.items() if k != "recommendation"}


def render_cases(cases: dict) -> str:
    return json.dumps(cases, indent=1, ensure_ascii=False)
