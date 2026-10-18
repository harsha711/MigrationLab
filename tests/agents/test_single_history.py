"""Single-agent memory compaction (docs/decisions/single-history.md)."""

import json

import pytest

from migproto import plans, scenarios, staged
from migproto.agents import facts, loop, roles, single
from migproto.agents.budget import BudgetExceeded, ReviewBudget
from migproto.agents.llm import LLMError
from migproto.config import WORKTREES
from migproto.runlog import RunLog

PLAN = {"plan_id": "p", "stages": [
    {"id": "e", "kind": "sql", "phase": "expand", "sql": ["SELECT 1"]},
    {"id": "d", "kind": "deploy_new", "migration": "state_only"},
    {"id": "r", "kind": "retire_old"},
    {"id": "c", "kind": "sql", "phase": "contract", "sql": ["SELECT 2"]}]}


class HistoryLLM:
    """Fake model that keeps history the way llm.LLM.chat_json does and records each request."""
    model = "fake"
    base_url = "http://fake/v1"
    temperature = top_p = 1.0
    reasoning_enabled = force_nonempty_content = False

    def __init__(self, revisions=3, bad_revision=None):
        self.budget = ReviewBudget(max_revisions=revisions)
        self.sent: list[tuple[str, list[dict]]] = []
        self.bad_revision = bad_revision
        self.plans = 0

    def serving_metadata(self):
        return {}

    def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                  history=None):
        if history is None:
            history = [{"role": "system", "content": system}]
        elif not history:
            history.append({"role": "system", "content": system})
        history.append({"role": "user", "content": user})
        kind = (response_schema or {}).get("name") or ("report" if "Summarize" in user
                                                       else "investigate")
        self.sent.append((kind, [dict(m) for m in history]))
        transcript.append({"attempt": 0, "messages": list(history), "schema_valid": True})
        if kind == "migration_plan":
            self.plans += 1
            if self.bad_revision and self.plans > 1:
                raise self.bad_revision
            obj = dict(PLAN, plan_id=f"plan-{self.plans}")
            obj = validator(obj) if validator else obj
        elif kind == "tester_proposal":
            obj = {"checks": [], "custom_cases": []}
        elif kind == "report":
            obj = {"summary": "model-written summary", "suspected_cause": "model cause"}
        else:
            obj = {"findings": [], "implicit_paths": []}
        history.append({"role": "assistant", "content": json.dumps(obj)})
        return obj


def _fake_runner(monkeypatch):
    n = {"i": 0}

    def run(log, plan, tests, provenance, keep_db=False, scenario=None):
        n["i"] += 1
        i = n["i"]
        log.check(f"check-{i}", False, provenance="runner", error=f"attempt-{i}-error " * 150)
        return {"ok": False, "plan_id": plan["plan_id"], "halted": f"halt-{i}", "stages": [],
                "failed_checks": []}
    monkeypatch.setattr(staged, "run", run)
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [
        {"ref": "saleor/account/models.py:1", "line": "note = x", "context": "note = x"}])


def _review(monkeypatch, tmp_path, llm, topology="single"):
    _fake_runner(monkeypatch)
    log = RunLog("t", root=tmp_path)
    return log, loop.run_review(log, "llm", llm=llm, topology=topology)


def _chars(messages):
    return sum(len(m["content"]) for m in messages)


# C1: an invalid planner revision ends the review as FAIL with the runner's earlier results.

@pytest.mark.parametrize("error", [LLMError("no JSON object found"),
                                   plans.PlanError("stage 'd': unknown kind")])
@pytest.mark.parametrize("topology", ["roles", "single"])
def test_invalid_revision_is_fail_with_runner_results(monkeypatch, tmp_path, error, topology):
    llm = HistoryLLM(revisions=1, bad_revision=error)
    log, result = _review(monkeypatch, tmp_path, llm, topology=topology)
    assert result["verdict"] == "FAIL" and result["ok"] is False
    assert result["reason"].startswith("revision invalid: ")
    assert [a["failed_checks"] for a in result["attempts"]] == [["check-1"]]
    events = [json.loads(line) for line in (log.dir / "events.jsonl").open()]
    assert any(e["kind"] == "revision_invalid" and e["attempt"] == 2 for e in events)


def test_budget_exhausted_during_revision_stays_incomplete(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=1, bad_revision=BudgetExceeded("token budget exhausted"))
    _, result = _review(monkeypatch, tmp_path, llm)
    assert result["verdict"] == "INCOMPLETE"


def test_invalid_first_plan_stays_error(monkeypatch, tmp_path):
    class BadFirst(HistoryLLM):
        def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                      history=None):
            if (response_schema or {}).get("name") == "migration_plan":
                raise LLMError("no JSON object found")
            return super().chat_json(system, user, transcript, validator, response_schema,
                                     history)
    _, result = _review(monkeypatch, tmp_path, BadFirst())
    assert result["verdict"] == "ERROR" and result["attempts"] == []


# C2: gather_hits searches the scenario's OLD app version, never a new build.

def test_old_version_is_the_code_under_review():
    assert facts.old_version("rename-note") == "old"
    assert facts.old_version("raw-sql-rename") == "raw-sql-rename/old"
    new_builds = {v.version for s in scenarios.SCENARIOS.values() for v in s.variants.values()}
    assert not {facts.old_version(s) for s in scenarios.SCENARIOS} & new_builds


def test_old_version_refuses_a_new_build(monkeypatch):
    import dataclasses
    sc = scenarios.get("raw-sql-rename")
    bad = dataclasses.replace(sc, old_version=sc.variants["apply"].version)
    monkeypatch.setitem(scenarios.SCENARIOS, sc.id, bad)
    with pytest.raises(ValueError, match="new-version build"):
        facts.old_version(sc.id)


def test_review_searches_the_scenario_old_worktree(monkeypatch, tmp_path):
    searched = []
    _fake_runner(monkeypatch)
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: searched.append(a) or [])
    loop.run_review(RunLog("t", root=tmp_path), "llm", llm=HistoryLLM(revisions=0),
                    scenario="raw-sql-rename")
    assert searched == [(WORKTREES["raw-sql-rename/old"],)]


def test_gather_hits_drops_experiment_annotations(tmp_path):
    path = tmp_path / "saleor" / "account" / "views.py"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join([
        "def resolve(root):",
        "    # EXPERIMENT (manual): this note column read is the trap",
        "    # continued explanation of the trap",
        "    x = 1",
        "    return note_row(note)",
        "    # unrelated comment about note",
    ]))
    hits = roles.gather_hits(tmp_path)
    assert [h["ref"] for h in hits] == ["saleor/account/views.py:5",
                                        "saleor/account/views.py:6"]
    blob = json.dumps(hits)
    assert "EXPERIMENT" not in blob and "trap" not in blob
    assert "x = 1" in hits[0]["context"]
