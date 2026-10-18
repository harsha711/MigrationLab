"""Configuration I: revise from the best attempt, majority-reject stop, budget-sized rounds."""

import threading

from migproto import staged
from migproto.agents import loop, roles
from migproto.agents.budget import ReviewBudget
from migproto.runlog import RunLog


def _plan(plan_id="p", recommendation=None):
    out = {"plan_id": plan_id, "stages": [
        {"id": "e", "kind": "sql", "phase": "expand",
         "sql": ["ALTER TABLE account_user ADD COLUMN customer_note text"]},
        {"id": "d", "kind": "deploy_new"}, {"id": "r", "kind": "retire_old"}]}
    if recommendation:
        out["recommendation"] = recommendation
    return out


class FakeLLM:
    model = "nemotron-test"
    base_url = "http://test/v1"
    temperature = 1.0
    top_p = 0.95
    reasoning_enabled = True
    force_nonempty_content = True

    def __init__(self, budget, recommendation=None, tokens=10):
        self.budget = budget
        self.recommendation = recommendation
        self.tokens = tokens
        self.prompts = []
        self.lock = threading.Lock()

    def serving_metadata(self):
        return {}

    def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                  history=None):
        self.budget.begin_call()
        self.budget.finish_call({"total_tokens": self.tokens}, 0.0, valid=True)
        if response_schema and response_schema["name"] == "migration_plan":
            with self.lock:
                self.prompts.append((getattr(self, "trace_name", ""), user))
            return validator(_plan(recommendation=self.recommendation))
        if response_schema:
            return {"checks": [], "custom_cases": []}
        return {"findings": [], "implicit_paths": [], "summary": "s"}


def _runner(failures_per_attempt, recommendation=None):
    runs = []

    def fake(log, plan, tests, provenance, keep_db=False, scenario=None):
        n = failures_per_attempt[len(runs)]
        runs.append(plan["plan_id"])
        for j in range(n):
            log.check(f"case.{j}", False, provenance="runner")
        if not n:
            log.check("case.ok", True, provenance="runner")
        out = {"ok": n == 0, "plan_id": plan["plan_id"], "halted": None,
               "stages": [{"stage": "e"}, {"stage": "d"}],
               "failed_checks": [{"name": f"case.{j}"} for j in range(n)]}
        if recommendation:
            out["recommendation"] = recommendation
        return out
    return runs, fake


def test_single_revises_from_its_best_attempt_not_the_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    runs, fake = _runner([1, 15, 0])
    monkeypatch.setattr(staged, "run", fake)
    llm = FakeLLM(ReviewBudget(max_revisions=3, max_model_calls=40, max_total_tokens=10**6))
    log = RunLog("test", root=tmp_path)
    result = loop.run_review(log, "llm", llm=llm,
                             request={"change": {"description": "rename note"},
                                      "agent_topology": "single"})
    assert result["verdict"] == "PASS"
    revisions = [u for _, u in llm.prompts][1:]
    # Revision 1 follows attempt 1 (the only one); revision 2 goes back to attempt 1 (1 failed
    # check), not attempt 2 (15), and says why.
    assert '"attempt": 1' in revisions[0] and "is your attempt" not in revisions[0]
    assert "'Previous plan' is your attempt 1" in revisions[1]
    assert '"plan_id": "a1-p"' in revisions[1].split("Execution feedback")[0]
    assert loop.REVISION_RULE[:40] in revisions[1]
    events = (log.dir / "events.jsonl").read_text()
    assert '"kind": "revision_base"' in events and '"base_attempt": 1' in events


def test_progress_never_ranks_an_early_halt_above_a_plan_that_got_further():
    early = {"stages_run": ["e"], "failed_checks": ["stage.b.executes"]}
    further = {"stages_run": ["e", "d"], "failed_checks": [f"c{i}" for i in range(18)]}
    assert loop._progress(further) > loop._progress(early)


def test_parallel_stops_when_most_planners_and_the_runner_reject(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    runs, fake = _runner([1] * 30, recommendation="reject")
    monkeypatch.setattr(staged, "run", fake)
    llm = FakeLLM(ReviewBudget(max_revisions=5, max_model_calls=40, max_total_tokens=10**6),
                  recommendation="reject")
    result = loop.run_review(RunLog("test", root=tmp_path), "llm", llm=llm,
                             scenario="unique-last-name",
                             request={"change": {"description": "unique last name"},
                                      "agent_topology": "parallel",
                                      "scenario": "unique-last-name"})
    assert result["verdict"] == "FAIL" and len(runs) == 3      # one round, then stop
    assert result["runner_recommendation"] == "reject"


def test_round_narrows_to_the_furthest_lineages_when_budget_is_short(tmp_path):
    log = RunLog("test", root=tmp_path)

    def lineage(failed):
        return {"history": [{"record": {"stages_run": ["e", "d"],
                                        "failed_checks": ["x"] * failed}}]}
    lineages = [lineage(9), lineage(1), lineage(4)]
    budget = ReviewBudget(max_model_calls=40, max_total_tokens=100)
    budget.model_calls, budget.total_tokens = 7, 70        # 10 per call, 30 left = 1 lineage
    assert loop._affordable(lineages, budget, log, "agent:x", 2) == [1]
    budget.total_tokens = 30                               # 70 left = 3 lineages
    assert loop._affordable(lineages, budget, log, "agent:x", 2) == [0, 1, 2]
    budget.total_tokens, budget.model_calls = 30, 36       # 4 calls left = 2 lineages
    assert loop._affordable(lineages, budget, log, "agent:x", 2) == [1, 2]
