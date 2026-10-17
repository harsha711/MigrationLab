"""v1 runner records (docs/team/handoff-01.md) pass through the adapter, store and queries.

Uses tests/memory/fixtures/v1-synthetic-staged: SYNTHETIC, hand-written to the handoff's
field list, not runner output. Swap in a real examples/v1/ run once one is exported.
"""

import sqlite3
from pathlib import Path

import pytest

from migproto.memory import adapter, query, store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v1-synthetic-staged"
RUN = FIXTURE.name
A1 = f"{RUN}/a1"


@pytest.fixture(scope="module")
def conn():
    c = store.connect(":memory:")
    store.ingest(c, FIXTURE)
    return c


def obs(conn, name):
    return query.query_observations(conn, run_id=RUN, check_name=name)[0]


def test_v1_fields_recorded_not_derived(conn):
    o = obs(conn, "case.old_write_new_read")
    assert o["scope_source"] == "recorded_v1"
    assert o["observation_id"] == "20261017-150000-staged/a1/case.old_write_new_read#7"
    assert o["attempt_id"] == A1             # normalized to the directory, like v0 rule 1
    assert o["raw"]["attempt_id"] == "20261017-150000-staged/a1"  # recorded value kept
    assert o["stage_id"] == "deploy_new"     # from step_id
    assert o["plan_id"] == "scripted-v1"
    assert (o["outcome"], o["check_type"], o["app_scope"]) == ("fail", "assertion", "both")
    assert o["app_commit"]["new"] == "synthetic-new-sha"
    assert o["schema_state"]["schema_hash"] == "synthetic-hash-expand"
    assert o["database_snapshot_id"] == "seed:0000synthetic0"
    assert o["expected"] == {"value": "old wrote this"}
    assert o["observed"] == {"value": "seed#194"}
    assert [s["app_version"] for s in o["steps"]] == ["old", "new-staged"]


def test_outcomes_distinguish_fail_refused_skipped(conn):
    assert obs(conn, "stage.contract.preconditions")["outcome"] == "refused"
    skipped = obs(conn, "stage.contract.not_run")
    assert (skipped["status"], skipped["outcome"]) == ("SKIP", "skipped")
    run = query.run_detail(conn, RUN)
    a = run["attempts"][0]
    assert a["outcome_counts"] == {"pass": 5, "fail": 1, "refused": 1, "skipped": 1}
    assert a["outcome_counts"] == run["summary"]["outcome_counts"]
    # SKIP is not a failure: checks_failed keeps its v0 meaning (status FAIL).
    assert a["checks_failed"] == len(run["summary"]["checks_failed"]) == 2


def test_v1_filters(conn):
    assert [o["check_name"] for o in query.query_observations(conn, outcome="refused")] == [
        "stage.contract.preconditions"]
    gates = query.query_observations(conn, run_id=RUN, check_type="safety_gate")
    assert {o["check_name"] for o in gates} == {"plan.schema_valid", "stage.contract.preconditions"}
    both = query.query_observations(conn, run_id=RUN, app_scope="both")
    assert {o["check_name"] for o in both} == {"case.old_write_new_read", "case.new_write_old_read"}


def test_v0_checks_keep_unknown_v1_fields():
    c = store.connect(":memory:")
    store.ingest(c, FIXTURE.parents[2].parent / "examples" / "scripted-sync-failure")
    for o in query.query_observations(c):
        assert o["outcome"] is None and o["check_type"] is None and o["app_scope"] is None
    assert query.query_observations(c, app_scope="both") == []


def test_matrix_and_meta_carry_v1(conn):
    m = query.matrix(conn, RUN, A1)
    cell = next(r for r in m["rows"] if r["check_name"] == "case.old_write_new_read")
    assert (cell["column"], cell["outcome"]) == ("old -> new-staged", "fail")
    assert {d["outcome"] for d in m["db_checks"]} == {"pass", "refused", "skipped"}
    r = query.results_panel(conn, RUN)
    assert r["contract_version"] == adapter.V1
    assert r["recorded_run_id"] == "20261017-150000-staged"
    assert r["database_snapshot_ids"] == ["seed:0000synthetic0"]


def test_old_store_gains_v1_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE observations (observation_id TEXT PRIMARY KEY, run_id TEXT, "
                "attempt_id TEXT, plan_id TEXT, stage_id TEXT, check_name TEXT, status TEXT, "
                "outcome TEXT, expectation TEXT, schema_state_json TEXT, "
                "definition_provenance TEXT, executed_by TEXT, scope_source TEXT, seq INTEGER, "
                "error TEXT, raw_json TEXT)")
    old.execute("INSERT INTO observations (observation_id, status) VALUES ('kept#1', 'PASS')")
    old.commit()
    old.close()
    c = store.connect(path)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(observations)")}
    assert set(store.ADDED_COLUMNS["observations"]) <= cols
    assert c.execute("SELECT status FROM observations WHERE observation_id = 'kept#1'"
                     ).fetchone()[0] == "PASS"
    store.ingest(c, FIXTURE)
    assert query.get_observation(c, "20261017-150000-staged/a1/case.old_write_new_read#7")
