"""Scenario registry, plan rules for apply-migration plans, and runner helpers (no database)."""

import pytest

from migproto import checks, plans, scenarios, staged  # noqa: F401  (staged.recommendation)
from migproto.config import WORKTREES
from migproto.saleor_app import AppServer, create_customer_request, token_expired


def plan(*stages):
    return {"plan_id": "p", "stages": list(stages)}


DEPLOY_APPLY = {"id": "deploy_new", "kind": "deploy_new", "migration": "apply"}
RETIRE = {"id": "retire_old", "kind": "retire_old"}


def test_registry_is_consistent():
    assert scenarios.DEFAULT == "rename-note"
    rename = scenarios.get(None)
    assert rename.rename_hooks and rename.cases == tuple(checks.MANDATORY_AFTER_DEPLOY)
    assert rename.variant("state_only").version == "new-staged"
    assert rename.variant("apply").version == "new-unsafe"
    for s in scenarios.SCENARIOS.values():
        assert "apply" in s.variants, s.id
        assert all(k in ("apply", "state_only") or k.startswith("apply-") for k in s.variants)
        assert all(c in checks.CATALOGUE for c in s.cases), s.id
        assert s.old_version in WORKTREES
        assert set(s.not_null_phases) <= set(scenarios.ALL_PHASES)
        for v in s.variants.values():
            assert v.version in WORKTREES and v.migration.startswith("0107_")
    assert len(scenarios.SCENARIOS) == 23
    assert "raw-sql-rename/old" in scenarios.variant_versions()
    with pytest.raises(KeyError):
        scenarios.get("no-such-scenario")
    with pytest.raises(KeyError):
        scenarios.get("add-optional-column").variant("state_only")


def test_rename_catalogue_unchanged():
    # The rename scenario must keep running exactly the original 8 mandatory cases.
    assert checks.MANDATORY_AFTER_DEPLOY == list(scenarios.RENAME_CASES)


def test_apply_plan_needs_no_expand_or_contract():
    p = plan(DEPLOY_APPLY, RETIRE)
    assert plans.validate_plan(p) is p and plans.deploy_migration(p) == "apply"


def test_state_only_plan_still_needs_expand_and_contract():
    with pytest.raises(plans.PlanError, match="expand"):
        plans.validate_plan(plan({"id": "d", "kind": "deploy_new"}, RETIRE))
    with pytest.raises(plans.PlanError, match="migration must be one of"):
        plans.validate_plan(plan({**DEPLOY_APPLY, "migration": "yolo"}, RETIRE))
    with pytest.raises(plans.PlanError, match="only the deploy_new stage"):
        plans.validate_plan(plan(DEPLOY_APPLY, {**RETIRE, "migration": "apply"}))
    with pytest.raises(plans.PlanError, match="retire_old"):
        plans.validate_plan(plan(DEPLOY_APPLY))


def test_rollback_check_and_variant_rules():
    rollback = {"id": "rb", "kind": "rollback_check"}
    p = plan({**DEPLOY_APPLY, "variant": "apply-reversible"}, rollback, RETIRE)
    assert plans.validate_plan(p) and plans.deploy_variant(p) == "apply-reversible"
    assert plans.deploy_variant(plan(DEPLOY_APPLY, RETIRE)) == "apply"
    assert plans.deploy_variant(plan({"id": "d", "kind": "deploy_new"}, RETIRE)) == "state_only"
    with pytest.raises(plans.PlanError, match="after deploy_new and before retire_old"):
        plans.validate_plan(plan(rollback, DEPLOY_APPLY, RETIRE))
    with pytest.raises(plans.PlanError, match="at most one"):
        plans.validate_plan(plan(DEPLOY_APPLY, rollback, {**rollback, "id": "rb2"}, RETIRE))
    with pytest.raises(plans.PlanError, match="only the deploy_new stage may set variant"):
        plans.validate_plan(plan(DEPLOY_APPLY, {**RETIRE, "variant": "x"}))
    with pytest.raises(KeyError):
        scenarios.get("irreversible-data-migration").variant("apply-nope")


def test_scripted_plans_valid_and_labelled():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "proposals"
    for label_file in (root / "manual" / "scenarios").glob("*.json"):
        scenario = scenarios.get(label_file.stem)
        labels = json.loads(label_file.read_text(encoding="utf-8"))["plans"]
        for name in labels:
            p = json.loads((root / "scripted" / scenario.id / name).read_text(encoding="utf-8"))
            plans.validate_plan(p)
            scenario.variant(plans.deploy_variant(p))   # the plan's variant exists


def test_recommendation():
    sql = {"id": "e", "kind": "sql", "phase": "expand", "sql": ["SELECT 1"]}
    assert staged.recommendation(True, plan(DEPLOY_APPLY, RETIRE)) == "accept_as_is"
    assert staged.recommendation(True, plan(sql, DEPLOY_APPLY, RETIRE)) == "staged_rollout"
    assert staged.recommendation(False, plan(DEPLOY_APPLY, RETIRE)) == "reject"


def test_sql_builders_use_safe_identifiers():
    count, rows = checks.disagreement_sql(("note", "customer_note"))
    assert "note IS DISTINCT FROM customer_note" in count and "LIMIT 5" in rows
    assert checks.null_count_sql("marketing_opt_in").endswith("marketing_opt_in IS NULL")
    for bad in ("note; DROP TABLE x", "Note", "1abc"):
        with pytest.raises(ValueError):
            checks.null_count_sql(bad)


def test_create_customer_without_note_field():
    q, v = create_customer_request("a@b.test", "hi")
    assert "note:$note" in q and v == {"email": "a@b.test", "note": "hi"}
    q, v = create_customer_request("a@b.test", None, include_note=False)
    assert "note" not in q and v == {"email": "a@b.test"}


def test_expired_token_is_refreshed_once(monkeypatch):
    assert token_expired({"errors": [{"message": "ExpiredSignatureError: Signature has expired"}]})
    assert not token_expired({"errors": [{"message": "column note does not exist"}]})
    assert not token_expired({"data": {}})
    server = AppServer.__new__(AppServer)
    server._token, server._token_at = "stale", 0.0
    tokens = iter(["fresh-1", "fresh-2"])
    monkeypatch.setattr(AppServer, "token", lambda self: (self._token or next(tokens), None))
    calls = []

    def post(self, query, variables, token):
        calls.append(token)
        if token == "stale":
            return {"errors": [{"message": "ExpiredSignatureError: Signature has expired"}]}
        return {"data": {"ok": True}}

    monkeypatch.setattr(AppServer, "_post", post)
    assert server.gql("query{x}") == {"data": {"ok": True}}
    assert calls == ["stale", "fresh-1"]


class _Res:
    def __init__(self, ok, value=None, error=None):
        self.ok, self.value, self.error = ok, value, error


class _FakeServer:
    def __init__(self, write_ok, read_ok=True):
        self.write_ok, self.read_ok, self.calls = write_ok, read_ok, []

    def alive(self):
        return True

    def write_language(self, pk, code):
        self.calls.append(("write_language", pk, code))
        return _Res(self.write_ok, code if self.write_ok else None,
                    None if self.write_ok else "rejected")

    def read_language(self, pk):
        self.calls.append(("read_language", pk))
        return _Res(self.read_ok, "EN" if self.read_ok else None,
                    None if self.read_ok else "Enum cannot represent value: 'tlh'")


class _Log:
    def __init__(self):
        self.checks = []

    def check(self, name, passed, **kw):
        self.checks.append((name, passed, kw))
        return passed


def _lang_ctx(new, old):
    return checks.Context(log=_Log(), dbname="x", servers={"new": new, "old": old},
                          expected={}, _free_seed=[101, 102, 103])


def test_try_write_lang_rejection_is_tolerated_but_old_read_must_succeed():
    steps = checks.CATALOGUE["new_lang_write_old_read"]
    ctx = _lang_ctx(_FakeServer(write_ok=False), _FakeServer(write_ok=True))
    assert checks.run_case(ctx, "new_lang_write_old_read", steps)          # readers-first shape
    ctx = _lang_ctx(_FakeServer(write_ok=True), _FakeServer(write_ok=True, read_ok=False))
    assert not checks.run_case(ctx, "new_lang_write_old_read", steps)      # writer breaks old read
    name, passed, kw = ctx.log.checks[-1]
    assert name == "case.new_lang_write_old_read" and not passed
    assert kw["steps"][1]["error"].startswith("Enum cannot represent")


def test_split_note_helpers_round_trip_every_note():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "gen", Path(__file__).resolve().parents[1] / "scripts" / "make_scenario_patches.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    ns = {}
    exec(gen.SPLIT_HELPERS, ns)   # the exact code that goes into the Saleor patch
    samples = [None, "", "plain", "Ótimo: Produto bom,\r\nentrega rápida.", "a: b: c", "x: ",
               ": leading", "no colon-space:here", "👍🏻👌🏻", "Bom.: Recebi o produto."]
    for note in samples:
        assert ns["compose_note"](*ns["split_note"](note)) == note, note
    assert ns["split_note"]("Bom.: Recebi") == ("Bom.", "Recebi")
    assert ns["split_note"]("plain") == (None, "plain")


def test_composed_note_sql_matches_python_helper():
    # The SQL used by preservation_expr must express the same composition as compose_note.
    assert "note_title IS NULL THEN note_body" in scenarios.COMPOSED_NOTE_SQL
    assert "note_title || ': ' || coalesce(note_body, '')" in scenarios.COMPOSED_NOTE_SQL


def test_stage_options_for_batching_and_lock_retries():
    sql_stage = {"id": "e", "kind": "sql", "phase": "expand", "sql": ["SELECT 1"]}
    contract = {"id": "c", "kind": "sql", "phase": "contract", "sql": ["SELECT 1"]}
    base = [{"id": "d", "kind": "deploy_new"}, RETIRE, contract]
    assert plans.validate_plan(plan({**sql_stage, "pause_ms": 50, "retry_on_lock_timeout": 100},
                                    *base))
    for bad in ({"pause_ms": 99999}, {"retry_on_lock_timeout": -1}, {"pause_ms": "50"},
                {"retry_on_lock_timeout": True}):
        with pytest.raises(plans.PlanError):
            plans.validate_plan(plan({**sql_stage, **bad}, *base))
    with pytest.raises(plans.PlanError, match="only sql stages may set pause_ms"):
        plans.validate_plan(plan(sql_stage, {"id": "d", "kind": "deploy_new", "pause_ms": 5},
                                 RETIRE, contract))


def test_load_summary_statistics():
    from migproto import load
    samples = [(i * 0.1, float(ms), True, None) for i, ms in enumerate(range(10, 110, 10))]
    samples.append((2.0, 900.0, False, "IntegrityError"))
    s = load.summarize(samples)
    assert (s["writes"], s["errors"], s["max_ms"]) == (11, 1, 900.0)
    assert s["p50_ms"] == 60.0 and s["p95_ms"] == 900.0   # nearest rank of 11 samples
    assert s["worst"][0]["ms"] == 900.0 and s["first_errors"] == ["IntegrityError"]
    assert load.summarize([])["max_ms"] is None


def test_load_generator_rewrites_existing_values():
    import time as _time
    from migproto import load

    class Server:
        def __init__(self):
            self.writes = []

        def write_note(self, pk, value):
            self.writes.append((pk, value))
            _time.sleep(0.001)
            return _Res(True, value)

    server = Server()
    gen = load.LoadGenerator(server, {1: "a", 2: "b", 3: "c"}, threads=2).start()
    _time.sleep(0.1)
    stats = gen.stop()
    assert stats["writes"] > 10 and stats["errors"] == 0
    assert set(server.writes) <= {(1, "a"), (2, "b"), (3, "c")}   # data never changes


def test_load_generator_records_exceptions_as_errors():
    # Regression: a write blocked past the HTTP timeout raised ReadTimeout, which silently killed
    # the load thread, so a fully blocked stage looked clean (C5 unsafe, run 20261017-211020).
    import time as _time
    from migproto import load

    class TimingOut:
        def write_note(self, pk, value):
            _time.sleep(0.01)
            raise TimeoutError("Read timed out. (read timeout=60)")

    gen = load.LoadGenerator(TimingOut(), {1: "a", 2: "b"}, threads=2).start()
    _time.sleep(0.1)
    stats = gen.stop()
    assert stats["writes"] >= 4 and stats["errors"] == stats["writes"]
    assert stats["first_errors"][0].startswith("TimeoutError: Read timed out")


def test_large_table_scenarios_are_flagged():
    c_ids = {"big-backfill", "plain-index", "int-to-bigint", "long-transaction",
             "schema-and-backfill", "set-not-null"}
    for sid in c_ids:
        s = scenarios.get(sid)
        assert s.load and s.lock_budget_ms == 500
    assert scenarios.get("long-transaction").long_transaction == ("expand", 10.0)
    assert not any(s.load for s in scenarios.SCENARIOS.values() if s.id not in c_ids)


def test_note_free_new_app_skips_note_cases():
    class Log:
        def __init__(self):
            self.skipped = []

        def skip(self, name, reason, **kw):
            self.skipped.append((name, reason))

    ctx = checks.Context(log=Log(), dbname="x", servers={}, expected={},
                         new_app_note_ops=False)
    assert checks.run_case(ctx, "old_write_new_read", checks.CATALOGUE["old_write_new_read"])
    assert ctx.log.skipped == [("case.old_write_new_read", "new app version has no note field")]
