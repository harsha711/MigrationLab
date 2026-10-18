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
    assert len(scenarios.SCENARIOS) == 14
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


def test_state_only_plan_needs_one_effective_sql_stage():
    with pytest.raises(plans.PlanError, match="at least one sql stage"):
        plans.validate_plan(plan({"id": "d", "kind": "deploy_new"}, RETIRE))
    # One stage in either phase is enough (drop-column needs only a contract stage).
    drop = {"id": "c", "kind": "sql", "phase": "contract",
            "sql": ["ALTER TABLE account_user DROP COLUMN note"]}
    assert plans.validate_plan(plan({"id": "d", "kind": "deploy_new"}, RETIRE, drop))
    add = {"id": "e", "kind": "sql", "phase": "expand",
           "sql": ["SET lock_timeout = '5s'", "ALTER TABLE account_user ADD COLUMN x int"]}
    assert plans.validate_plan(plan(add, {"id": "d", "kind": "deploy_new"}, RETIRE))
    # Filler and verification-only stages do nothing and are rejected, in any plan.
    for filler in (["SELECT 1"], ["SELECT note FROM account_user LIMIT 1"],
                   ["SET lock_timeout = '5s'"], ["BEGIN", "COMMIT"]):
        stage = {"id": "f", "kind": "sql", "phase": "expand", "sql": filler}
        with pytest.raises(plans.PlanError, match="has no effect"):
            plans.validate_plan(plan(stage, {"id": "d", "kind": "deploy_new"}, RETIRE, drop))
        with pytest.raises(plans.PlanError, match="has no effect"):
            plans.validate_plan(plan(stage, DEPLOY_APPLY, RETIRE))
    # Statements that may do work are kept.
    for real in (["SELECT setval('account_user_id_seq', 10)"], ["SELECT backfill_batch()"],
                 ["COMMENT ON TABLE account_user IS 'x'"], ["DO $$ BEGIN PERFORM 1; END $$"]):
        stage = {"id": "f", "kind": "sql", "phase": "expand", "sql": real}
        assert plans.validate_plan(plan(stage, {"id": "d", "kind": "deploy_new"}, RETIRE))
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
