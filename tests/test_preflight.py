"""Dry run and write probe on a throwaway S0 copy (needs local Postgres and the Saleor venv)."""

import json

import pytest

from migproto import db, preflight
from migproto.config import PROPOSALS


def _available():
    try:
        db.query("postgres", "SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="needs local Postgres")


def _writes(report, point):
    return next(p for p in report["probes"] if p["point"] == point)["writes"]


def test_staged_plan_syncs_both_directions_after_deploy():
    plan = json.loads((PROPOSALS / "scripted" / "plan_v2.json").read_text())
    report = preflight.dry_run("rename-note", plan)
    assert report["first_error"] is None
    for w in _writes(report, "after deploy_new migration"):
        assert len(set(w["after"].values())) == 1


def test_one_way_sync_is_visible_and_bad_sql_stops_the_dry_run():
    v1 = json.loads((PROPOSALS / "scripted" / "plan_v1.json").read_text())
    report = preflight.dry_run("rename-note", v1)
    assert any(len(set(w["after"].values())) == 2
               for w in _writes(report, "after deploy_new migration"))
    bad = json.loads(json.dumps(v1))
    bad["stages"][0]["sql"] = ["ALTER TABLE account_user ADD COLUMN customer_note nosuchtype"]
    report = preflight.dry_run("rename-note", bad)
    assert report["first_error"]["stage"] == bad["stages"][0]["id"]
    assert "nosuchtype" in report["first_error"]["error"]
    assert len(report["stages"]) == 1
