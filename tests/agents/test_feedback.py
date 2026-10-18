import json

from migproto.agents import loop, roles


def _record(i):
    # Shaped like a runner v1 check record, including the bulky scope fields.
    return {"t": 1.0, "kind": "check", "name": f"case.check_{i}", "status": "FAIL",
            "observation_id": f"run/a2/case.check_{i}#{i}", "outcome": "fail",
            "app_version": "new", "step_id": "deploy_new", "error": "read None",
            "expected": {"value": f"v{i}"}, "observed": {"value": None},
            "provenance": "runner", "contract_version": "migproto.contracts/v1",
            "database_snapshot_id": "seed:" + "f" * 16, "timestamp": "2026-10-17T00:00:00Z",
            "schema_state": {"table": "account_user", "note_columns": ["note", "customer_note"],
                             "triggers": ["sync"], "schema_hash": "x" * 16,
                             "account_migration": "0106_merge_20260807_0748"}}


def test_planner_sees_every_failed_check_and_the_tester_summary():
    failures = [_record(i) for i in range(20)]
    feedback = loop._feedback({"ok": False, "halted": "contract skipped", "stages": []},
                              {"summary": "TESTER-SUMMARY"}, failures)
    captured = {}

    class Stub:
        def chat_json(self, system, user, transcript, **kwargs):
            captured["user"] = user
            return {}

    roles.plan("llm", Stub(), {"findings": []}, [], feedback=feedback,
               previous={"plan_id": "p"})
    section = captured["user"][captured["user"].index("Execution feedback"):]
    assert all(f"case.check_{i}" in section for i in range(20))
    assert "TESTER-SUMMARY" in section and "read None" in section


def test_compact_check_keeps_diagnostics_and_drops_bulk():
    out = loop._compact_check(_record(1))
    assert out["expected"] == {"value": "v1"} and out["observed"] == {"value": None}
    assert out["schema"] == {"note_columns": ["note", "customer_note"], "triggers": ["sync"]}
    assert not {"timestamp", "database_snapshot_id", "provenance"} & set(out)
    assert len(json.dumps(out)) < len(json.dumps(_record(1))) / 2


def test_best_earlier_attempt_only_when_latest_regressed():
    from migproto.agents import loop
    best = {"attempt": 3, "passed": 40, "plan": {"plan_id": "p3"}, "failed": ["case.x"]}
    assert loop._best_earlier(None, 2, 10) is None
    assert loop._best_earlier(best, 3, 40) is None          # the latest is the best
    assert loop._best_earlier(best, 4, 40) is None          # not worse
    got = loop._best_earlier(best, 4, 31)
    assert got["latest_passed"] == 31 and got["plan"] == {"plan_id": "p3"}
    fb = loop._feedback({"ok": False, "plan_id": "p4"}, {"summary": "s"}, [], got)
    assert list(fb)[:2] == ["tester_summary", "best_earlier_attempt"]
    assert fb["best_earlier_attempt"]["attempt"] == 3
    assert fb["best_earlier_attempt"]["failed_checks"] == ["case.x"]
    assert "best_earlier_attempt" not in loop._feedback({"ok": False}, {}, [])


def test_sql_quoting_precheck():
    import pytest
    from migproto import plans
    from migproto.agents import facts

    def plan(*sql):
        return {"stages": [{"id": "e", "kind": "sql", "phase": "expand", "sql": list(sql)}]}

    facts.check_sql_quoting(plan("CREATE FUNCTION f() RETURNS trigger AS $$ BEGIN RETURN NEW; "
                                 "END; $$ LANGUAGE plpgsql", "SELECT $1::text"))
    facts.check_sql_quoting(plan("CREATE FUNCTION f() RETURNS int AS $body$ SELECT 1 $body$ "
                                 "LANGUAGE sql"))
    with pytest.raises(plans.PlanError, match="literally"):
        facts.check_sql_quoting(plan("CREATE FUNCTION f() AS \\$\\$ BEGIN END; \\$\\$"))
    with pytest.raises(plans.PlanError, match="unbalanced"):
        facts.check_sql_quoting(plan("CREATE FUNCTION f() RETURNS trigger AS $$ BEGIN",
                                     "RETURN NEW; END; $$ LANGUAGE plpgsql"))
