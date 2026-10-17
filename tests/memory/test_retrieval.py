"""prior_evidence: only earlier agent runs of the same change, never fixtures or scripted runs."""

import json
import shutil
from pathlib import Path

import pytest

from migproto.memory import retrieval, store

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "fixtures" / "owner2-synthetic-loop"
EXAMPLES = HERE.parents[1] / "examples"
CHANGE = {"table": "account_user", "old_column": "note", "new_column": "customer_note"}

AGENT = "20261017-170000-agent-loop-llm"
SCRIPTED = "20261017-170100-agent-loop-scripted"
OTHER_CHANGE = "20261017-170200-agent-loop-llm"
NO_REQUEST = "20261017-170300-agent-loop-llm"
CALLER = "20261017-170400-agent-loop-llm"


def _edit_json(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    for name in (AGENT, SCRIPTED, OTHER_CHANGE, NO_REQUEST, CALLER):
        shutil.copytree(SOURCE, root / name)
    _edit_json(root / SCRIPTED / "summary.json", lambda s: s.update(provenance="scripted"))
    _edit_json(root / OTHER_CHANGE / "agents" / "review-request.json",
               lambda r: r["change"].update(new_column="client_note"))
    (root / NO_REQUEST / "agents" / "review-request.json").unlink()
    return root


def test_only_agent_runs_of_same_change(tmp_path, runs):
    db = tmp_path / "memory.sqlite"
    # The UI may already have ingested the frozen fixtures into the same store.
    c = store.connect(db)
    store.ingest_all(c, sources=(EXAMPLES,))
    c.close()

    out = retrieval.prior_evidence(CHANGE, db=db, runs_root=runs, exclude_run_id=CALLER)

    assert [r["run_id"] for r in out["runs_considered"]] == [AGENT]
    assert out["excluded"] == {"fixtures": 4, "non_agent": 1,
                               "unknown_or_different_change": 2, "calling_run": 1}
    assert [o["observation_id"] for o in out["observations"]] == [
        f"{AGENT}/a1/retirement_gate#14"]
    item = out["observations"][0]
    assert item["plan_id"] == "a1-rename-note-001"
    assert item["plan_summary"] == "Synthetic plan a1-rename-note-001."
    assert item["stage_id"] == "contract" and item["msg"] == "old app still running"
    assert out["truncated"] is False


def test_examples_never_ingested_by_retrieval(tmp_path, runs):
    db = tmp_path / "fresh.sqlite"
    retrieval.prior_evidence(CHANGE, db=db, runs_root=runs)
    c = store.connect(db)
    kinds = {r[0] for r in c.execute("SELECT DISTINCT source_kind FROM runs")}
    assert kinds == {"local_run"}


def test_incomplete_change_matches_nothing(tmp_path, runs):
    out = retrieval.prior_evidence({"table": "account_user"}, db=tmp_path / "x.sqlite",
                                   runs_root=runs)
    assert out["observations"] == [] and out["runs_considered"] == []


def test_limit(tmp_path, runs):
    out = retrieval.prior_evidence(CHANGE, db=tmp_path / "y.sqlite", runs_root=runs, limit=1)
    assert len(out["observations"]) == 1 and out["truncated"] is True
