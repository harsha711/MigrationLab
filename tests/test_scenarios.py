"""Scenario registry, plan rules for apply-migration plans, and runner helpers (no database)."""

import pytest

from migproto import checks, plans, scenarios, staged
from migproto.config import WORKTREES
from migproto.saleor_app import create_customer_request


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
        assert set(s.variants) <= {"apply", "state_only"} and "apply" in s.variants
        assert all(c in checks.CATALOGUE for c in s.cases), s.id
        for v in s.variants.values():
            assert v.version in WORKTREES and v.migration.startswith("0107_")
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
