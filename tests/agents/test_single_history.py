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


def test_prompt_size_stays_bounded_over_four_attempts(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=3)
    _, result = _review(monkeypatch, tmp_path, llm)
    assert len(result["attempts"]) == 4
    reports = [m for kind, m in llm.sent if kind == "report"]
    plans_sent = [m for kind, m in llm.sent if kind == "migration_plan"]
    assert len(reports) == 4 and len(plans_sent) == 4
    assert max(len(m) for _, m in llm.sent) <= 8   # system + 3 kept exchanges + request
    # From the second revision on, each request holds the same kept exchanges; only the runner
    # note grows, by one line per attempt.
    assert 0 <= _chars(reports[3]) - _chars(reports[2]) < 500
    assert 0 <= _chars(plans_sent[3]) - _chars(plans_sent[2]) < 500
    steady = max(_chars(m) for _, m in llm.sent[:10])
    assert max(_chars(m) for _, m in llm.sent) < steady + 500


def test_uncompacted_history_would_grow(monkeypatch, tmp_path):
    """Control: the same review in roles topology resends nothing, single without compaction
    would resend everything; compaction keeps single within a small factor of roles."""
    llm = HistoryLLM(revisions=3)
    _review(monkeypatch, tmp_path, llm)
    single_total = sum(_chars(m) for _, m in llm.sent)
    full = sum(sum(_chars(m) for _, m in llm.sent[:i + 1]) for i in range(len(llm.sent)))
    assert single_total < full / 3


def test_latest_runner_evidence_is_always_present_in_full(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=3)
    _review(monkeypatch, tmp_path, llm)
    latest_evidence = None
    for kind, messages in llm.sent:
        request = messages[-1]["content"]
        if latest_evidence is not None:
            assert any(m["content"] == latest_evidence or m["content"].endswith(latest_evidence)
                       for m in messages), kind
        if kind == "report":
            assert "attempt-" in request and "halt-" in request
            latest_evidence = request


def test_first_turn_and_system_prompt_are_kept(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=3)
    _review(monkeypatch, tmp_path, llm)
    first_user = llm.sent[0][1][1]
    for _, messages in llm.sent:
        assert messages[0] == {"role": "system", "content": single.SYSTEM}
        assert messages[1] == first_user


def test_compaction_is_deterministic_and_recorded_in_transcript(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=3)
    log, _ = _review(monkeypatch, tmp_path, llm)
    tx = json.loads((log.dir / "agents" / "transcript-single.json").read_text())
    assert len(tx) == len(llm.sent)
    records = [r["history_compaction"] for r in tx]
    assert all(r["policy"] == single.POLICY for r in records)
    assert records[0]["replaced_turns"] == [] and records[2]["replaced_turns"] == []
    last = records[-1]
    assert last["kept_turns"] == ["investigator", "tester-report-3", "planner-4"]
    assert "planner-1" in last["replaced_turns"] and "tester-propose-4" in last["replaced_turns"]
    sent = "\n".join(m["content"] for m in llm.sent[-1][1])
    note = sent[sent.index("[Controller memory note."):sent.index("[End of memory note.]")]
    assert "attempt 1: plan a1-plan-1, FAIL, halted: halt-1; failed checks: check-1" in note
    assert "model-written" not in note and "model cause" not in note
    config = next(json.loads(line) for line in (log.dir / "events.jsonl").open()
                  if json.loads(line)["kind"] == "agent_config")
    assert config["history_compaction"] == single.POLICY


def test_failed_call_stores_no_turn():
    memory = single.Memory()
    history, _ = memory.begin()
    history.append({"role": "user", "content": "request"})   # chat_json raised after this
    memory.end("investigator")
    assert memory.turns == []


def test_roles_topology_has_no_compaction(monkeypatch, tmp_path):
    llm = HistoryLLM(revisions=1)
    log, _ = _review(monkeypatch, tmp_path, llm, topology="roles")
    assert all(len(m) == 2 for _, m in llm.sent)
    assert not (log.dir / "agents" / "transcript-single.json").exists()


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
