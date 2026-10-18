import copy
import json

import pytest

from migproto import plans, scenarios
from migproto.agents.roles import PLAN_SCHEMA, plan_schema
from migproto.config import PROPOSALS

SQL_SHAPE, DEPLOY_SHAPE, OTHER_SHAPE = PLAN_SCHEMA["schema"]["properties"]["stages"]["items"]["anyOf"]


def matches(schema: dict, value) -> bool:
    """Minimal JSON Schema check for the keywords PLAN_SCHEMA uses (no jsonschema dependency)."""
    if "anyOf" in schema:
        return any(matches(s, value) for s in schema["anyOf"])
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(props):
            return False
        return all(matches(props[k], v) for k, v in value.items() if k in props)
    if kind == "array":
        return (isinstance(value, list) and len(value) >= schema.get("minItems", 0)
                and all(matches(schema.get("items", {}), v) for v in value))
    if kind == "string":
        return isinstance(value, str) and len(value) >= schema.get("minLength", 0)
    if kind == "boolean":
        return isinstance(value, bool)
    return True


def validator_accepts(plan: dict) -> bool:
    try:
        plans.validate_plan(plan)
        return True
    except plans.PlanError:
        return False


def _base() -> dict:
    return {"plan_id": "p", "stages": [
        {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
        {"id": "d", "kind": "deploy_new"},
        {"id": "r", "kind": "retire_old"},
        {"id": "c", "kind": "sql", "phase": "contract", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
    ]}


def _scripted_plans() -> list[dict]:
    # Tests may read scripted plans; prompts never do (see test_prompt_isolation.py).
    out = []
    for path in sorted((PROPOSALS / "scripted").rglob("*.json")):
        obj = json.loads(path.read_text())
        if isinstance(obj, dict) and "stages" in obj:
            obj.pop("provenance", None)   # runner-only key, never produced by a model
            out.append(obj)
    return out


def test_only_sql_stages_can_carry_sql():
    # plans.validate_plan rejects a `sql` key, even an empty list, on non-sql stages;
    # Lightning emitted `"sql": []` there under the old single-shape schema.
    for shape in (DEPLOY_SHAPE, OTHER_SHAPE):
        assert "sql" not in shape["properties"]
        assert shape["additionalProperties"] is False
    kinds = {DEPLOY_SHAPE["properties"]["kind"]["const"], *OTHER_SHAPE["properties"]["kind"]["enum"]}
    assert kinds == plans.STAGE_KINDS - {"sql"}
    assert SQL_SHAPE["properties"]["kind"] == {"const": "sql"}
    assert {"phase", "sql"} <= set(SQL_SHAPE["required"])
    assert set(SQL_SHAPE["properties"]["phase"]["enum"]) == plans.SQL_PHASES


def test_only_deploy_new_can_set_migration_or_variant():
    assert set(DEPLOY_SHAPE["properties"]["migration"]["enum"]) == plans.DEPLOY_MIGRATIONS
    for shape in (SQL_SHAPE, OTHER_SHAPE):
        assert not {"migration", "variant"} & set(shape["properties"])


def test_schema_shapes_only_use_validator_keys():
    for shape in (SQL_SHAPE, DEPLOY_SHAPE, OTHER_SHAPE):
        assert set(shape["properties"]) <= plans.STAGE_KEYS
    # `recommendation` is the one agent-side key; the controller strips it before the runner.
    assert set(PLAN_SCHEMA["schema"]["properties"]) <= plans.PLAN_KEYS | {"recommendation"}
    json.dumps(PLAN_SCHEMA)


def test_schema_accepts_every_plan_the_validator_accepts():
    corpus = _scripted_plans()
    assert len(corpus) >= 20
    for plan in corpus:
        assert validator_accepts(plan), plan["plan_id"]
        assert matches(PLAN_SCHEMA["schema"], plan), plan["plan_id"]


def _mutations():
    def stage(i, **kw):
        p = _base()
        p["stages"][i].update(kw)
        return p

    yield "sql on deploy_new", stage(1, sql=[])
    yield "sql on retire_old", stage(2, sql=["SELECT 1"])
    yield "migration on retire_old", stage(2, migration="apply")
    yield "variant on sql", stage(0, variant="apply")
    yield "bad migration", stage(1, migration="later")
    yield "empty variant", stage(1, variant="")
    yield "empty sql list", stage(0, sql=[])
    yield "unknown stage key", stage(1, extra=True)
    yield "unknown phase", stage(0, phase="cleanup")
    p = _base()
    p["stages"].insert(2, {"id": "rb", "kind": "rollback_check", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]})
    yield "sql on rollback_check", p
    p = _base()
    p["stages"].insert(2, {"id": "rb", "kind": "rollback_check", "migration": "apply"})
    yield "migration on rollback_check", p


@pytest.mark.parametrize("name,plan", list(_mutations()))
def test_schema_rejects_stage_shapes_the_validator_rejects(name, plan):
    assert not validator_accepts(plan), name
    assert not matches(PLAN_SCHEMA["schema"], plan), name


def test_schema_allows_new_stage_fields_the_validator_accepts():
    p = _base()
    p["stages"][1].update(migration="apply", variant="apply-reversible")
    p["stages"].insert(2, {"id": "rb", "kind": "rollback_check", "description": "x"})
    accept = {"plan_id": "a", "recommendation": "accept_as_is", "stages": [
        {"id": "d", "kind": "deploy_new", "migration": "apply"},
        {"id": "r", "kind": "retire_old"}]}
    for plan in (p, accept):
        assert matches(PLAN_SCHEMA["schema"], plan)
        assert validator_accepts({k: v for k, v in plan.items() if k != "recommendation"})


def test_scenario_schema_limits_variants_and_requires_apply_without_state_only():
    sc = scenarios.get("add-optional-column")
    schema = plan_schema(tuple(sc.variants))["schema"]
    deploy = schema["properties"]["stages"]["items"]["anyOf"][1]
    assert "variant" not in deploy["properties"]
    assert deploy["properties"]["migration"]["enum"] == ["apply"]
    assert "migration" in deploy["required"]
    plan = {"plan_id": "a", "stages": [{"id": "d", "kind": "deploy_new"},
                                       {"id": "r", "kind": "retire_old"}]}
    assert not matches(schema, plan)
    ok = copy.deepcopy(plan)
    ok["stages"][0]["migration"] = "apply"
    assert matches(schema, ok)
    b4 = plan_schema(tuple(scenarios.get("irreversible-data-migration").variants))["schema"]
    assert b4["properties"]["stages"]["items"]["anyOf"][1]["properties"]["variant"]["enum"] == [
        "apply-reversible"]


def test_scenario_schema_rejects_variant_contradicting_migration():
    sc = scenarios.get("drop-column")
    schema = plan_schema(tuple(sc.variants))["schema"]
    plan = {"plan_id": "a", "stages": [
        {"id": "d", "kind": "deploy_new", "migration": "state_only", "variant": "apply"},
        {"id": "r", "kind": "retire_old"}]}
    assert not matches(schema, plan)
    del plan["stages"][0]["variant"]
    assert matches(schema, plan)


def test_rename_contract_explains_both_builds_like_other_scenarios():
    # Configuration D: rename prompts state what "migration" deploys, in VARIANT_TEXT's words.
    from migproto.agents import facts, roles
    flat = " ".join(roles.PLAN_CONTRACT.split())
    for key, text in facts.VARIANT_TEXT.items():
        assert f'"{key}" (set "migration": "{key}"' in flat
        assert " ".join(text.split()) in flat
    assert '"migration": "state_only" | "apply"' in roles.PLAN_CONTRACT
