"""Owner 2 controller output (structured claims, lineage, request, budgets) is ingested.

Uses tests/memory/fixtures/owner2-synthetic-loop: SYNTHETIC, generated to the shape of
Owner 2's run_review output on feature/aavritti/small-models. Not runner output. Its
directory name differs from the recorded run_id on purpose (like an exported fixture).
"""

import json
import shutil
from pathlib import Path

import pytest

from migproto.memory import store, query

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "owner2-synthetic-loop"
RUN = FIXTURE.name
RECORDED = "20261017-160000-agent-loop-llm"


@pytest.fixture(scope="module")
def conn():
    c = store.connect(":memory:")
    store.ingest(c, FIXTURE)
    return c


@pytest.fixture(scope="module")
def detail(conn):
    return query.run_detail(conn, RUN)


def attempt(detail, label):
    return next(a for a in detail["attempts"] if a["attempt"] == label)


def test_structured_claim_with_resolved_and_dangling_evidence(detail):
    claims = attempt(detail, "a1")["claims"]
    structured = [c for c in claims if c["structured"]]
    assert len(structured) == 1
    claim = structured[0]
    assert claim["claim_id"] == f"{RECORDED}/a1/tester-report/0"
    assert (claim["role"], claim["kind"]) == ("tester", "diagnosis")
    links = {e["observation_id"]: e for e in claim["evidence"]}
    # Recorded prefix rewritten to the directory name because that makes it resolve.
    gate = links[f"{RUN}/a1/retirement_gate#14"]
    assert gate["resolved"] == 1 and gate["check_name"] == "retirement_gate"
    assert gate["status"] == "FAIL"
    # An ID that matches nothing stays as recorded and is marked unresolved.
    dangling = links[f"{RECORDED}/a1/case.never_recorded#999"]
    assert dangling["resolved"] == 0 and dangling["check_name"] is None


def test_free_text_duplicate_dropped_only_where_structured(detail):
    a1_roles = [(c["role"], c["structured"]) for c in attempt(detail, "a1")["claims"]]
    assert ("tester-report", 0) not in a1_roles
    a2_roles = [(c["role"], c["structured"]) for c in attempt(detail, "a2")["claims"]]
    assert ("tester-report", 0) in a2_roles   # no structured claim for a2 -> free text kept


def test_plan_lineage(detail):
    a2_plan = attempt(detail, "a2")["plans"][0]
    assert a2_plan["parent_plan_id"] == "a1-rename-note-001"
    assert a2_plan["solution_origin"] == "generated"
    assert attempt(detail, "a1")["plans"][0]["parent_plan_id"] is None


def test_request_and_meta(detail):
    meta = detail["meta"]
    assert meta["request"]["change"]["new_column"] == "customer_note"
    assert meta["scenario"].startswith("Rename Django field User.note")
    assert meta["topology"] == "roles"
    assert meta["checkpoint_revision"] == "bee7596271d1495f6992ae224aefde4410e816b8"
    assert meta["serving_version"] == "0.27.1" and meta["dev_only_model"] is False


def test_results_prefer_agent_totals(detail):
    r = detail["results"]
    assert (r["model_call_count"], r["total_tokens"], r["model_seconds"]) == (8, 21000, 31.2)
    assert r["valid_proposal_rate"] == 1.0
    assert r["budget"]["max_total_tokens"] == 40000
    assert r["budget_remaining"]["tokens"] == 19000
    assert r["sampling"]["temperature"] == 1.0


def test_incomplete_verdict_passes_through(tmp_path):
    run = tmp_path / "incomplete-run"
    shutil.copytree(FIXTURE, run)
    summary = json.loads((run / "summary.json").read_text())
    summary.update(verdict="INCOMPLETE", ok=False, reason="token budget exhausted")
    (run / "summary.json").write_text(json.dumps(summary))
    c = store.connect(":memory:")
    store.ingest(c, run)
    d = query.run_detail(c, "incomplete-run")
    assert d["meta"]["verdict"] == "INCOMPLETE"
    assert d["results"]["reason"] == "token budget exhausted"


def test_in_progress_run_is_not_ingested(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(FIXTURE, runs / "finished")
    shutil.copytree(FIXTURE, runs / "still-running")
    (runs / "still-running" / "summary.json").unlink()
    c = store.connect(":memory:")
    assert store.ingest_all(c, sources=(runs,)) == ["finished"]
