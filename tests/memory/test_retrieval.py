"""prior_evidence: only earlier runs of the same model and change, never fixtures or scripted runs."""

import json
import shutil
from pathlib import Path

import pytest

from migproto.memory import retrieval, store

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "fixtures" / "owner2-synthetic-loop"
EXAMPLES = HERE.parents[1] / "examples"
CHANGE = {"table": "account_user", "old_column": "note", "new_column": "customer_note"}
MODEL = "nemotron-3.5-lightning"  # the fixture's provenance is agent:nemotron-3.5-lightning
STANDIN_MODEL = "migproto-qwen3-4b-instruct-24k"

AGENT = "20261017-170000-agent-loop-llm"
SCRIPTED = "20261017-170100-agent-loop-scripted"
OTHER_CHANGE = "20261017-170200-agent-loop-llm"
NO_REQUEST = "20261017-170300-agent-loop-llm"
CALLER = "20261017-170400-agent-loop-llm"
STANDIN = "20261017-170500-agent-loop-llm"
OTHER_SCENARIO = "20261017-170600-agent-loop-llm"


def _edit_json(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    for name in (AGENT, SCRIPTED, OTHER_CHANGE, NO_REQUEST, CALLER, STANDIN, OTHER_SCENARIO):
        shutil.copytree(SOURCE, root / name)
    _edit_json(root / SCRIPTED / "summary.json", lambda s: s.update(provenance="scripted"))
    _edit_json(root / STANDIN / "summary.json",
               lambda s: s.update(model=STANDIN_MODEL, provenance=f"agent:{STANDIN_MODEL}"))
    _edit_json(root / AGENT / "agents" / "review-request.json",
               lambda r: r.update(scenario="rename-note"))
    # Same table and columns, different scenario id: only a scenario filter separates it.
    _edit_json(root / OTHER_SCENARIO / "agents" / "review-request.json",
               lambda r: r.update(scenario="raw-sql-rename"))
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

    out = retrieval.prior_evidence(CHANGE, model=MODEL, scenario="rename-note", db=db,
                                   runs_root=runs, exclude_run_id=CALLER)

    assert [r["run_id"] for r in out["runs_considered"]] == [AGENT]
    assert out["excluded"] == {"fixtures": 4, "non_agent": 1, "different_model": 1,
                               "unknown_or_different_change": 2,
                               "unknown_or_different_scenario": 1, "calling_run": 1}
    assert out["model"] == MODEL and out["scenario"] == "rename-note"
    assert [o["observation_id"] for o in out["observations"]] == [
        f"{AGENT}/a1/retirement_gate#14"]
    item = out["observations"][0]
    assert item["plan_id"] == "a1-rename-note-001"
    assert item["plan_summary"] == "Synthetic plan a1-rename-note-001."
    assert item["stage_id"] == "contract" and item["msg"] == "old app still running"
    assert out["truncated"] is False


def test_examples_never_ingested_by_retrieval(tmp_path, runs):
    db = tmp_path / "fresh.sqlite"
    retrieval.prior_evidence(CHANGE, model=MODEL, db=db, runs_root=runs)
    c = store.connect(db)
    kinds = {r[0] for r in c.execute("SELECT DISTINCT source_kind FROM runs")}
    assert kinds == {"local_run"}


def test_incomplete_change_matches_nothing(tmp_path, runs):
    out = retrieval.prior_evidence({"table": "account_user"}, model=MODEL,
                                   db=tmp_path / "x.sqlite", runs_root=runs)
    assert out["observations"] == [] and out["runs_considered"] == []


def test_limit(tmp_path, runs):
    out = retrieval.prior_evidence(CHANGE, model=MODEL, db=tmp_path / "y.sqlite",
                                   runs_root=runs, limit=1)
    assert len(out["observations"]) == 1 and out["truncated"] is True


def test_only_the_calling_model(tmp_path, runs):
    """A local stand-in's runs never reach a Nemotron prompt, and vice versa."""
    out = retrieval.prior_evidence(CHANGE, model=STANDIN_MODEL, db=tmp_path / "s.sqlite",
                                   runs_root=runs)
    assert [r["run_id"] for r in out["runs_considered"]] == [STANDIN]
    assert {o["run_id"] for o in out["observations"]} == {STANDIN}

    out = retrieval.prior_evidence(CHANGE, model=MODEL, db=tmp_path / "n.sqlite",
                                   runs_root=runs)
    assert STANDIN not in {r["run_id"] for r in out["runs_considered"]}
    assert out["excluded"]["different_model"] == 1


def test_model_prefix_does_not_match(tmp_path, runs):
    out = retrieval.prior_evidence(CHANGE, model="nemotron", db=tmp_path / "p.sqlite",
                                   runs_root=runs)
    assert out["runs_considered"] == [] and out["excluded"]["different_model"] == 6


def test_without_scenario_filter_scenario_is_not_required(tmp_path, runs):
    out = retrieval.prior_evidence(CHANGE, model=MODEL, db=tmp_path / "a.sqlite",
                                   runs_root=runs, exclude_run_id=CALLER)
    assert [r["run_id"] for r in out["runs_considered"]] == [OTHER_SCENARIO, AGENT]
    assert out["excluded"]["unknown_or_different_scenario"] == 0


def test_request_without_scenario_is_unknown(tmp_path, runs):
    # CALLER's request has no `scenario` field: unknown scope, never assumed to be rename-note.
    out = retrieval.prior_evidence(CHANGE, model=MODEL, scenario="rename-note",
                                   db=tmp_path / "u.sqlite", runs_root=runs)
    assert CALLER not in {r["run_id"] for r in out["runs_considered"]}
    assert out["excluded"]["unknown_or_different_scenario"] == 2


@pytest.mark.parametrize("model", [None, ""])
def test_model_is_required(tmp_path, runs, model):
    with pytest.raises(ValueError, match="model"):
        retrieval.prior_evidence(CHANGE, model=model, db=tmp_path / "r.sqlite", runs_root=runs)
