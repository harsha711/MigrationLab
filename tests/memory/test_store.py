"""SQLite store: append-only ingest and exact-scope queries over the examples/ fixtures."""

from pathlib import Path

import pytest

from migproto.memory import query, store

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TABLES = ("runs", "attempts", "plans", "stages", "observations", "observation_steps",
          "claims", "claim_evidence", "events")


@pytest.fixture(scope="module")
def conn():
    c = store.connect(":memory:")
    store.ingest_all(c, sources=(EXAMPLES,))
    return c


def counts(c):
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def test_ingest_is_idempotent(conn):
    before = counts(conn)
    store.ingest_all(conn, sources=(EXAMPLES,))
    assert counts(conn) == before
    assert before["observations"] == 7 + 29 + 33 + 71


def test_attempt_level_counts(conn):
    a = {x["attempt_id"]: x for x in query.run_detail(conn, "live-ordering-revision")["attempts"]}
    assert a["live-ordering-revision/a1"]["checks_total"] == 30
    assert a["live-ordering-revision/a1"]["checks_failed"] == 1
    assert a["live-ordering-revision/a1"]["failed"] == ["retirement_gate"]
    assert a["live-ordering-revision/a2"]["checks_total"] == 41
    assert a["live-ordering-revision/a2"]["checks_failed"] == 0
    assert a["live-ordering-revision/a2"]["plans"][0]["body"]["revision_notes"]


def test_exact_filters(conn):
    rows = query.query_observations(conn, run_id="scripted-sync-failure", stage_id="deploy_new",
                                    app_version="old", status="FAIL")
    assert rows and all(o["stage_id"] == "deploy_new" and "old" in o["app_versions"]
                        for o in rows)
    # Unknown scope never matches a concrete value: S0 has no schema state at all.
    s0 = query.query_observations(conn, run_id="scripted-sync-failure", stage_id="S0")
    assert all(o["schema_state"] is None for o in s0)
    assert query.query_observations(conn, run_id="no-such-run") == []


def test_fixture_runs_can_be_excluded(conn):
    assert query.query_observations(conn, source_kind="local_run") == []


def test_matrix_sync_failure_direction(conn):
    m = query.matrix(conn, "scripted-sync-failure", "scripted-sync-failure/a1")
    deploy = [r for r in m["rows"] if r["stage_id"] == "deploy_new"]
    old_to_new = [r for r in deploy if r["column"] == "old -> new-staged"]
    new_to_old = [r for r in deploy if r["column"] == "new-staged -> old"]
    assert old_to_new and all(r["status"] == "FAIL" for r in old_to_new)
    assert new_to_old and all(r["status"] == "PASS" for r in new_to_old)
    assert {d["check_name"] for d in m["db_checks"]} >= {"deploy_new.columns_agree"}


def test_matrix_keeps_attempts_apart(conn):
    a1 = query.matrix(conn, "live-ordering-revision", "live-ordering-revision/a1")
    a2 = query.matrix(conn, "live-ordering-revision", "live-ordering-revision/a2")
    assert {r["observation_id"] for r in a1["rows"]}.isdisjoint(
        {r["observation_id"] for r in a2["rows"]})
    assert any(d["check_name"] == "retirement_gate" and d["status"] == "FAIL"
               for d in a1["db_checks"])


def test_check_history_is_per_scope(conn):
    hist = query.check_history(conn, "case.old_write_new_read")
    assert {h["run_id"] for h in hist} == {"scripted-sync-failure", "scripted-staged-success",
                                           "live-ordering-revision"}
    # Opposite outcomes in different runs stay separate rows, not a merged verdict.
    assert {h["status"] for h in hist} == {"PASS", "FAIL"}
    assert len(hist) == 4


def test_results_panel_from_saved_data(conn):
    live = query.results_panel(conn, "live-ordering-revision")
    assert live["model_call_count"] == 8 and live["total_tokens"] == 30036
    assert live["serving_backend"] is None and live["gym"] is None
    scripted = query.results_panel(conn, "scripted-staged-success")
    assert scripted["model_call_count"] is None and scripted["total_tokens"] is None
