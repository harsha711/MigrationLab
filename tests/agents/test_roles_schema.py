import json

from migproto import plans
from migproto.agents.roles import PLAN_SCHEMA

SQL_SHAPE, OTHER_SHAPE = PLAN_SCHEMA["schema"]["properties"]["stages"]["items"]["anyOf"]


def test_only_sql_stages_can_carry_sql():
    # plans.validate_plan rejects a `sql` key, even an empty list, on deploy_new / retire_old;
    # Lightning emitted `"sql": []` there under the old single-shape schema.
    assert "sql" not in OTHER_SHAPE["properties"]
    assert OTHER_SHAPE["additionalProperties"] is False
    assert set(OTHER_SHAPE["properties"]["kind"]["enum"]) == plans.STAGE_KINDS - {"sql"}
    assert SQL_SHAPE["properties"]["kind"] == {"const": "sql"}
    assert {"phase", "sql"} <= set(SQL_SHAPE["required"])


def test_schema_shapes_only_use_validator_keys():
    for shape in (SQL_SHAPE, OTHER_SHAPE):
        assert set(shape["properties"]) <= plans.STAGE_KEYS
    json.dumps(PLAN_SCHEMA)
