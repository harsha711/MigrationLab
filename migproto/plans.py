"""Plan and tester-proposal schemas. Anything outside the schema is rejected."""

import json
from pathlib import Path

from . import db

STAGE_KINDS = {"sql", "deploy_new", "retire_old", "rollback_check"}
SQL_PHASES = {"expand", "backfill", "contract"}
PLAN_KEYS = {"plan_id", "summary", "write_consistency", "stages", "provenance", "revision_notes"}
STAGE_KEYS = {"id", "kind", "phase", "description", "sql", "repeat_until_zero_rows", "migration",
              "variant", "pause_ms", "retry_on_lock_timeout"}
MAX_PAUSE_MS = 5000
MAX_LOCK_RETRIES = 200
DEPLOY_MIGRATIONS = {"state_only", "apply"}
APPS = {"old", "new"}
OPS = {"read", "write", "create", "clear"}
MAX_STAGES = 12
MAX_STATEMENTS = 20
MAX_CASES = 12
MAX_STEPS = 12


class PlanError(ValueError):
    pass


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise PlanError("plan must be a JSON object")
    extra = set(plan) - PLAN_KEYS
    if extra:
        raise PlanError(f"unknown plan keys: {sorted(extra)}")
    for key in ("plan_id", "stages"):
        if key not in plan:
            raise PlanError(f"missing plan key {key!r}")
    stages = plan["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        raise PlanError(f"stages must be a list of 1..{MAX_STAGES}")
    seen = set()
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            raise PlanError(f"stage {i} must be an object")
        extra = set(st) - STAGE_KEYS
        if extra:
            raise PlanError(f"stage {i}: unknown keys {sorted(extra)}")
        sid, kind = st.get("id"), st.get("kind")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise PlanError(f"stage {i}: id must be a unique non-empty string")
        seen.add(sid)
        if kind not in STAGE_KINDS:
            raise PlanError(f"stage {sid}: kind must be one of {sorted(STAGE_KINDS)}")
        if kind == "sql":
            if st.get("phase") not in SQL_PHASES:
                raise PlanError(f"stage {sid}: phase must be one of {sorted(SQL_PHASES)}")
            sql = st.get("sql")
            if (not isinstance(sql, list) or not 1 <= len(sql) <= MAX_STATEMENTS
                    or not all(isinstance(s, str) and s.strip() for s in sql)):
                raise PlanError(f"stage {sid}: sql must be 1..{MAX_STATEMENTS} non-empty strings")
            for s in sql:
                try:
                    db.guard_sql(s)
                except db.GuardError as exc:
                    raise PlanError(f"stage {sid}: {exc}") from exc
        elif "sql" in st:
            raise PlanError(f"stage {sid}: only sql stages may carry sql")
        for key, limit in (("pause_ms", MAX_PAUSE_MS), ("retry_on_lock_timeout", MAX_LOCK_RETRIES)):
            if key in st:
                if kind != "sql":
                    raise PlanError(f"stage {sid}: only sql stages may set {key}")
                if not isinstance(st[key], int) or isinstance(st[key], bool) \
                        or not 0 <= st[key] <= limit:
                    raise PlanError(f"stage {sid}: {key} must be an integer 0..{limit}")
        if "migration" in st:
            if kind != "deploy_new":
                raise PlanError(f"stage {sid}: only the deploy_new stage may set migration")
            if st["migration"] not in DEPLOY_MIGRATIONS:
                raise PlanError(f"stage {sid}: migration must be one of {sorted(DEPLOY_MIGRATIONS)}")
        if "variant" in st:
            if kind != "deploy_new":
                raise PlanError(f"stage {sid}: only the deploy_new stage may set variant")
            if not isinstance(st["variant"], str) or not st["variant"]:
                raise PlanError(f"stage {sid}: variant must be a non-empty string")
    kinds = [st["kind"] for st in stages]
    for required in ("deploy_new", "retire_old"):
        if kinds.count(required) != 1:
            raise PlanError(f"plan must contain exactly one {required} stage")
    if kinds.count("rollback_check") > 1:
        raise PlanError("plan may contain at most one rollback_check stage")
    if "rollback_check" in kinds and not (
            kinds.index("deploy_new") < kinds.index("rollback_check") < kinds.index("retire_old")):
        raise PlanError("rollback_check must come after deploy_new and before retire_old")
    if deploy_migration(plan) == "apply":
        # The deployed version's own Django migration changes the schema; SQL stages are optional
        # (e.g. "accept as-is" plans: deploy_new + retire_old). The contract gate still applies.
        return plan
    phases = {st.get("phase") for st in stages if st["kind"] == "sql"}
    for required in ("expand", "contract"):
        if required not in phases:
            raise PlanError(f"plan must contain a sql stage with phase {required!r}")
    return plan


def deploy_migration(plan: dict) -> str:
    """How the deploy_new stage applies the new version's migration (default state_only)."""
    deploy = next(st for st in plan["stages"] if st.get("kind") == "deploy_new")
    return deploy.get("migration", "state_only")


def deploy_variant(plan: dict) -> str:
    """Which scenario variant to deploy: an explicit `variant`, else the migration mode."""
    deploy = next(st for st in plan["stages"] if st.get("kind") == "deploy_new")
    return deploy.get("variant") or deploy.get("migration", "state_only")


def validate_tests(tests: dict | None) -> dict:
    if tests is None:
        return {"checks": [], "custom_cases": []}
    if not isinstance(tests, dict):
        raise PlanError("tester proposal must be a JSON object")
    extra = set(tests) - {"checks", "custom_cases", "rationale", "provenance"}
    if extra:
        raise PlanError(f"unknown tester keys: {sorted(extra)}")
    checks = tests.get("checks", [])
    cases = tests.get("custom_cases", [])
    if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
        raise PlanError("checks must be a list of catalogue ids")
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise PlanError(f"custom_cases must be a list of at most {MAX_CASES}")
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "steps", "description"}:
            raise PlanError(f"bad custom case {case!r}")
        steps = case.get("steps")
        if not isinstance(case.get("id"), str) or not isinstance(steps, list) \
                or not 1 <= len(steps) <= MAX_STEPS:
            raise PlanError(f"custom case needs id and 1..{MAX_STEPS} steps")
        for step in steps:
            validate_step(step)
    return tests


def validate_step(step: dict) -> None:
    if not isinstance(step, dict) or set(step) - {"app", "op", "user", "value"}:
        raise PlanError(f"bad step {step!r}")
    if step.get("app") not in APPS or step.get("op") not in OPS:
        raise PlanError(f"step needs app in {sorted(APPS)} and op in {sorted(OPS)}: {step!r}")
    if not isinstance(step.get("user"), str):
        raise PlanError(f"step needs a user label: {step!r}")
    if step["op"] in ("write", "create"):
        value = step.get("value")
        if value is not None and (not isinstance(value, str) or len(value) > 5000):
            raise PlanError(f"step value must be a string up to 5000 chars or null: {step!r}")
