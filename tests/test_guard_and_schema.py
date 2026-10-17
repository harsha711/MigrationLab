import copy
import json

import pytest

from migproto import db, plans
from migproto.config import PROPOSALS


def load(name):
    return json.loads((PROPOSALS / "scripted" / name).read_text())


@pytest.mark.parametrize(
    ("_case", "host", "dbname"),
    [
        ("remote_host", "10.0.0.5", "migproto_run_x"),
        ("seed_db", "127.0.0.1", "migproto_seed"),
        ("postgres_db", "localhost", "postgres"),
    ],
)
def test_guard_target_rejects(_case, host, dbname):
    with pytest.raises(db.GuardError):
        db.guard_target(host, dbname)


def test_guard_target_accepts_disposable():
    db.guard_target("127.0.0.1", "migproto_run_abc")


@pytest.mark.parametrize(
    "statement",
    [
        "DROP DATABASE saleor",
        "drop table account_user",
        "TRUNCATE account_user",
        "COPY account_user TO PROGRAM 'sh'",
        "CREATE EXTENSION dblink",
        "ALTER SYSTEM SET fsync = off",
        "CREATE ROLE x",
        "GRANT ALL ON account_user TO public",
        "SELECT pg_read_file('/etc/passwd')",
        "CREATE FUNCTION f() RETURNS int LANGUAGE plpython3u AS $$ return 1 $$",
    ],
)
def test_guard_sql_denylist(statement):
    with pytest.raises(db.GuardError):
        db.guard_sql(statement)


def test_guard_sql_allows_plan_statements():
    for name in ("plan_v1.json", "plan_v2.json"):
        for stage in load(name)["stages"]:
            for statement in stage.get("sql", []):
                db.guard_sql(statement)


def test_scripted_plans_valid():
    plans.validate_plan(load("plan_v1.json"))
    plans.validate_plan(load("plan_v2.json"))
    plans.validate_tests(load("tester.json"))


def _mutate(fn):
    plan = copy.deepcopy(load("plan_v2.json"))
    fn(plan)
    return plan


@pytest.mark.parametrize(
    ("_case", "mutation"),
    [
        ("unknown_top_key", lambda p: p.update(execute_python="import os")),
        ("unknown_stage_key", lambda p: p["stages"][0].update(shell="rm -rf /")),
        ("bad_kind", lambda p: p["stages"][2].update(kind="run_script")),
        ("missing_retire", lambda p: p["stages"].pop(3)),
        ("duplicate_deploy", lambda p: p["stages"].insert(0, dict(p["stages"][2]) | {"id": "d2"})),
        ("no_contract", lambda p: p["stages"].pop(4)),
        ("denylisted_sql", lambda p: p["stages"][4]["sql"].append("DROP TABLE account_user")),
        ("sql_on_app_stage", lambda p: p["stages"][2].update(sql=["SELECT 1"])),
        ("empty_sql", lambda p: p["stages"][0].update(sql=[])),
    ],
)
def test_plan_schema_rejects(_case, mutation):
    with pytest.raises(plans.PlanError):
        plans.validate_plan(_mutate(mutation))


def test_tests_schema_rejects_bad_step():
    with pytest.raises(plans.PlanError):
        plans.validate_tests({"custom_cases": [{"id": "x", "steps": [{"app": "both", "op": "read",
                                                                       "user": "A"}]}]})
