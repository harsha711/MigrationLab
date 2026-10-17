"""Trigger behaviour of the scripted plans on a scratch table shaped like account_user.

Old-app writes are emulated as UPDATEs that list `note` but not `customer_note` (what
Django's save() emits for the old model); new-app writes list only `customer_note`.
"""

import json
import uuid

import pytest

from migproto import db
from migproto.config import PROPOSALS, RUN_DB_PREFIX


def _db_available():
    try:
        db.exists("postgres")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="local postgres not running")


def expand_sql(plan_name):
    plan = json.loads((PROPOSALS / "scripted" / plan_name).read_text())
    return [s for st in plan["stages"] if st.get("phase") in ("expand", "backfill")
            for s in st["sql"]]


@pytest.fixture
def scratch():
    name = f"{RUN_DB_PREFIX}trigger_{uuid.uuid4().hex[:8]}"
    db.create(name)
    db.run_guarded(name, [
        "CREATE TABLE account_user (id serial PRIMARY KEY, email text, note text NULL)",
        "INSERT INTO account_user (email, note) VALUES ('a', 'seed a'), ('b', NULL), ('c', 'seed c')",
    ])
    yield name
    db.drop(name)


def row(name, pk):
    return db.query(name, "SELECT note, customer_note FROM account_user WHERE id=%s", (pk,))[0]


def old_write(name, pk, value):
    db.run_guarded(name, [f"UPDATE account_user SET email = email, note = {_lit(value)} WHERE id = {pk}"])


def new_write(name, pk, value):
    db.run_guarded(name, [f"UPDATE account_user SET email = email, customer_note = {_lit(value)} "
                          f"WHERE id = {pk}"])


def _lit(value):
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def test_v2_backfill_copies_existing(scratch):
    db.run_guarded(scratch, expand_sql("plan_v2.json"))
    assert row(scratch, 1) == ("seed a", "seed a")
    assert row(scratch, 2) == (None, None)


@pytest.mark.parametrize(
    ("_case", "writer", "value"),
    [
        ("old_set", old_write, "from old"),
        ("new_set", new_write, "from new"),
        ("old_clear", old_write, None),
        ("new_clear", new_write, None),
    ],
)
def test_v2_syncs_both_writers(scratch, _case, writer, value):
    db.run_guarded(scratch, expand_sql("plan_v2.json"))
    writer(scratch, 1, value)
    assert row(scratch, 1) == (value, value)


def test_v2_inserts_from_both_versions(scratch):
    db.run_guarded(scratch, expand_sql("plan_v2.json"))
    db.run_guarded(scratch, [
        "INSERT INTO account_user (email, note) VALUES ('old', 'old insert')",
        "INSERT INTO account_user (email, customer_note) VALUES ('new', 'new insert')",
    ])
    rows = db.query(scratch, "SELECT email, note, customer_note FROM account_user "
                             "WHERE email IN ('old', 'new') ORDER BY email")
    assert rows == [("new", "new insert", "new insert"), ("old", "old insert", "old insert")]


def test_v2_both_columns_changed_new_wins(scratch):
    db.run_guarded(scratch, expand_sql("plan_v2.json"))
    db.run_guarded(scratch, ["UPDATE account_user SET note = 'n', customer_note = 'cn' WHERE id = 3"])
    assert row(scratch, 3) == ("cn", "cn")


def test_v1_misses_old_writer(scratch):
    db.run_guarded(scratch, expand_sql("plan_v1.json"))
    old_write(scratch, 1, "from old")
    assert row(scratch, 1) == ("from old", "seed a")
    new_write(scratch, 3, "from new")
    assert row(scratch, 3) == ("from new", "from new")
