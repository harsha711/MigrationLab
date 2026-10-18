"""Plan and tester-proposal schemas. Anything outside the schema is rejected."""

import json
from pathlib import Path

from . import db

STAGE_KINDS = {"sql", "deploy_new", "retire_old", "rollback_check"}
SQL_PHASES = {"expand", "backfill", "contract"}
PLAN_KEYS = {"plan_id", "summary", "write_consistency", "stages", "provenance", "revision_notes",
             "addressed_feedback"}
STAGE_KEYS = {"id", "kind", "phase", "description", "sql", "repeat_until_zero_rows", "migration",
              "variant"}
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
    addressed = plan.get("addressed_feedback")
    if addressed is not None:
        if not isinstance(addressed, list):
            raise PlanError("addressed_feedback must be a list")
        for item in addressed:
            if (not isinstance(item, dict) or set(item) != {"index", "action", "reason"}
                    or not isinstance(item["index"], int) or item["index"] < 0
                    or item["action"] not in {"adopted", "adapted", "declined"}
                    or not isinstance(item["reason"], str)):
                raise PlanError("addressed_feedback items need index, action and reason")
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
    check_stage_effects(plan)
    if deploy_migration(plan) == "apply":
        # The deployed version's own Django migration changes the schema; SQL stages are optional
        # (e.g. "accept as-is" plans: deploy_new + retire_old). The contract gate still applies.
        return plan
    # state_only: the deployed migration issues no SQL, so the plan's SQL stages own the schema
    # change. Some changes need only one side (a drop needs only a contract stage, an added
    # column only an expand stage), so one sql stage in any phase is enough.
    if "sql" not in kinds:
        raise PlanError('with "migration": "state_only" the plan needs at least one sql stage')
    return plan


# Statements that change nothing in the database: queries (whose results the runner never
# evaluates), session settings and transaction control.
_NO_EFFECT_NODES = {"VariableSetStmt", "VariableShowStmt", "TransactionStmt"}
# Read-only built-ins a verification query typically calls. A SELECT calling any other function
# (setval, set_config, pg_advisory_lock, a function the plan created) may do real work and is
# kept.
_READ_ONLY_FUNCS = {
    "count", "sum", "min", "max", "avg", "bool_and", "bool_or", "every", "array_agg",
    "string_agg", "json_agg", "jsonb_agg", "length", "char_length", "octet_length", "lower",
    "upper", "trim", "btrim", "substr", "substring", "now", "md5", "to_char", "concat",
    "format", "pg_typeof", "current_setting", "pg_column_size",
}


def _no_effect(stmt) -> bool:
    from pglast import ast, visitors

    name = type(stmt).__name__
    if name in _NO_EFFECT_NODES:
        return True
    if not isinstance(stmt, ast.SelectStmt) or stmt.intoClause is not None:
        return False
    funcs = []

    class Calls(visitors.Visitor):
        def visit_FuncCall(self, ancestors, node):
            funcs.append(node.funcname[-1].sval.lower())

    Calls()(stmt)
    return all(f in _READ_ONLY_FUNCS for f in funcs)


def check_stage_effects(plan: dict) -> None:
    """Reject a sql stage whose every statement changes nothing (SELECT 1, a verification
    query, SET, SHOW, BEGIN/COMMIT). The runner would log it as an executed stage that did no
    work; query results are never evaluated, so checks belong in tester cases. Statements
    PostgreSQL's parser cannot read are left to the runner, which reports the error."""
    from pglast import parser

    for st in plan.get("stages", []):
        if st.get("kind") != "sql":
            continue
        try:
            stmts = [raw.stmt for s in st.get("sql") or [] for raw in parser.parse_sql(s)]
        except parser.ParseError:
            continue
        if stmts and all(_no_effect(s) for s in stmts):
            raise PlanError(
                f"stage {st.get('id')}: has no effect (only queries, SET/SHOW or transaction "
                "statements). The runner does not evaluate query results; put checks in tester "
                "cases and give every sql stage a statement that changes the schema or data, or "
                "remove the stage")


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
