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


def test_human_feedback_is_labelled_unverified_in_the_revision_prompt():
    captured = {}

    class Stub:
        def chat_json(self, system, user, transcript, **kwargs):
            captured["user"] = user
            return {}

    roles.plan("llm", Stub(), {"findings": []}, [], feedback={"failed_checks": []},
               previous={"plan_id": "p"}, human_feedback=[{
                   "kind": "issue", "text": "retain the old writer", "author": "Ada",
                   "at": "2026-10-18T00:00:00Z",
               }])
    assert "Reviewer feedback (human, unverified; the runner will check any change you make)" in captured["user"]
    assert "retain the old writer" in captured["user"]


def test_human_test_reaches_tester_without_replacing_mandatory_catalogue():
    captured = {}

    class Stub:
        def chat_json(self, system, user, transcript, **kwargs):
            captured["user"] = user
            return {"checks": [], "custom_cases": []}

    roles.propose_tests("llm", Stub(), {"plan_id": "p"}, [], human_tests=[{
        "kind": "test", "text": "alternate old and new writers", "author": "Ada",
        "at": "2026-10-18T00:00:00Z",
    }])
    assert "alternate old and new writers" in captured["user"]
    assert "ALWAYS runs this mandatory catalogue" in captured["user"]


def test_compact_check_keeps_diagnostics_and_drops_bulk():
    out = loop._compact_check(_record(1))
    assert out["expected"] == {"value": "v1"} and out["observed"] == {"value": None}
    assert out["schema"] == {"note_columns": ["note", "customer_note"], "triggers": ["sync"]}
    assert not {"timestamp", "database_snapshot_id", "provenance"} & set(out)
    assert len(json.dumps(out)) < len(json.dumps(_record(1))) / 2


def test_runner_facts_come_before_the_labelled_tester_hypothesis():
    fb = loop._feedback({"ok": False, "plan_id": "p2", "halted": "h"}, {"summary": "s"},
                        [_record(1)])
    keys = list(fb)
    assert keys.index("failed_checks") < keys.index("tester_hypothesis")
    assert fb["tester_hypothesis"]["report"] == {"summary": "s"}
    assert "not verified" in fb["tester_hypothesis"]["note"]
    assert "earlier_attempts" not in fb


def test_earlier_attempts_are_runner_facts_not_a_ranking():
    # Run 220028: attempt 1 halted early (few checks ran) but was closer than attempt 2.
    a1 = loop._attempt_record(1, {"plan_id": "a1-p"}, {"halted": "stage b failed to execute",
                              "halted_step": "b", "stages": [{"stage": "e"}]},
                              [{"name": "stage.b.executes"}])
    a2 = loop._attempt_record(2, {"plan_id": "a2-p"}, {"halted": "contract skipped",
                              "halted_step": "c", "stages": [{"stage": "e"}, {"stage": "d"}]},
                              [{"name": f"case.{i}"} for i in range(18)])
    fb = loop._feedback({"ok": False}, {}, [], [a1, a2])
    got = fb["earlier_attempts"]["attempts"]
    assert [r["attempt"] for r in got] == [1, 2]
    assert got[0]["plan"] == {"plan_id": "a1-p"} and got[0]["halted_step"] == "b"
    assert got[1]["stages_run"] == ["e", "d"] and len(got[1]["failed_checks"]) == 18
    assert "best" not in json.dumps(fb)


def test_feedback_fits_the_prompt_cap_by_dropping_optional_parts_first():
    big = {"plan_id": "x", "stages": [{"sql": ["SELECT 1;" * 400]}]}
    earlier = [loop._attempt_record(i, big, {}, []) for i in (1, 2, 3)]
    fb = loop._feedback({"ok": False}, {"summary": "KEEP"}, [_record(i) for i in range(5)],
                        [dict(r) for r in earlier])
    assert len(json.dumps(fb, indent=1)) <= roles.EVIDENCE_CHARS
    assert fb["tester_hypothesis"]["report"] == {"summary": "KEEP"}
    assert len(fb["failed_checks"]) == 5
    assert earlier[0]["plan"] == big            # the caller's records are not mutated
    fb = loop._fit({"failed_checks": [_record(i) for i in range(100)],
                    "tester_hypothesis": {"report": "KEEP"}}, limit=5000)
    assert fb["tester_hypothesis"] == {"report": "KEEP"} and "failed_checks_omitted" in fb
    assert len(json.dumps(fb, indent=1)) <= 5000


def test_backfill_termination_precheck():
    import pytest
    from migproto import plans
    from migproto.agents import facts

    def plan(sql, repeat=True):
        return {"stages": [{"id": "b", "kind": "sql", "phase": "expand", "sql": [sql],
                            "repeat_until_zero_rows": repeat}]}

    for ok in ("UPDATE account_user SET customer_note = note WHERE customer_note IS NULL "
               "AND note IS NOT NULL;",
               "UPDATE account_user SET customer_note = note "
               "WHERE customer_note IS DISTINCT FROM note",
               "UPDATE account_user SET customer_note = note WHERE note <> customer_note",
               "UPDATE account_user SET customer_note = COALESCE(note, '') "
               "WHERE customer_note IS NULL",
               "UPDATE account_user SET customer_note = note WHERE id IN (SELECT id FROM "
               "account_user WHERE customer_note IS NULL AND note IS NOT NULL LIMIT 1000)"):
        facts.check_backfill_terminates(plan(ok))
    facts.check_backfill_terminates(plan("UPDATE account_user SET customer_note = note;",
                                         repeat=False))
    for bad in ("UPDATE account_user SET customer_note = note WHERE customer_note IS NULL;",
                "UPDATE account_user SET customer_note = note WHERE note IS NOT NULL;",
                "UPDATE account_user SET customer_note = note;"):
        with pytest.raises(plans.PlanError, match="never reaches 0 rows"):
            facts.check_backfill_terminates(plan(bad))


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


def test_sql_syntax_precheck_uses_the_postgresql_parser():
    import pytest
    from migproto import plans
    from migproto.agents import facts

    def plan(*sql):
        return {"stages": [{"id": "e", "kind": "sql", "phase": "expand", "sql": list(sql)}]}

    fn = ("CREATE OR REPLACE FUNCTION f() RETURNS trigger AS $$ BEGIN IF NEW.note IS NULL "
          "THEN NEW.note := NEW.customer_note; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql")
    facts.check_sql_syntax(plan(
        fn, "CREATE TRIGGER t BEFORE INSERT OR UPDATE ON account_user FOR EACH ROW "
            "EXECUTE FUNCTION f()",
        "ALTER TABLE account_user ADD COLUMN customer_note text",
        "DO $$ BEGIN UPDATE account_user SET customer_note = note; END $$"))
    # Shapes that failed to execute on Lightning (runs 215440, 220035, 210420, 213633, 205627).
    for bad in ("UPDATE account_user SET customer_note = note WHERE customer_note IS NULL "
                "LIMIT 1000;",
                "CREATE TRIGGER t BEFORE UPDATE ON account_user FOR EACH ROW EXECUTE FUNCTION f;",
                "CREATE UNIQUE INDEX NOT VALID i ON account_user (last_name)",
                fn.replace("END IF; RETURN NEW;", "END IF; END; IF TG_OP = 'x' THEN END IF; "
                           "RETURN NEW;"),
                fn.replace("RETURN NEW; END;", "RETURN NEW;"),
                "DO $$ BEGIN LOOP UPDATE account_user SET customer_note = note LIMIT 1000; "
                "EXIT WHEN NOT FOUND; END LOOP; END $$"):
        with pytest.raises(plans.PlanError, match="PostgreSQL syntax error"):
            facts.check_sql_syntax(plan(bad))


def test_trigger_when_precheck():
    import pytest
    from migproto import plans
    from migproto.agents import facts

    def plan(*sql):
        return {"stages": [{"id": "e", "kind": "sql", "phase": "expand", "sql": list(sql)}]}

    head = "CREATE TRIGGER t BEFORE {} ON account_user {} WHEN ({}) EXECUTE FUNCTION f()"
    for ok in (head.format("UPDATE", "FOR EACH ROW", "OLD.note IS DISTINCT FROM NEW.note"),
               head.format("INSERT", "FOR EACH ROW", "NEW.customer_note IS NULL"),
               head.format("DELETE", "FOR EACH ROW", "OLD.note IS NOT NULL"),
               "CREATE TRIGGER t BEFORE INSERT OR UPDATE ON account_user FOR EACH ROW "
               "EXECUTE FUNCTION f()"):
        facts.check_trigger_when(plan(ok))
    # Shapes PostgreSQL refused on Lightning (runs 225329, 220035).
    for bad, match in ((head.format("INSERT OR UPDATE", "FOR EACH ROW",
                                    "OLD.note IS DISTINCT FROM NEW.note"), "INSERT"),
                       (head.format("DELETE", "FOR EACH ROW", "NEW.note IS NULL"), "DELETE"),
                       (head.format("UPDATE", "FOR EACH STATEMENT", "true"), "statement-level")):
        with pytest.raises(plans.PlanError, match=match):
            facts.check_trigger_when(plan(bad))
