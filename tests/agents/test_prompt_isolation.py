"""Rendered prompts for every scenario must not contain scripted answers or expected verdicts.

AGENTS.md: never expose proposals/scripted/**, proposals/manual/**, patches/ or examples/
outcomes to evaluated agents. This renders every role prompt of a full fake-model review
(both topologies, including the revision prompt) and searches it for those answers.
"""

import json

import pytest

from migproto import scenarios, staged
from migproto.agents import facts, loop, roles
from migproto.agents.budget import ReviewBudget
from migproto.config import PROPOSALS
from migproto.runlog import RunLog

FAKE_SQL = "SELECT 424242 AS fake_planner_statement"


def _scripted_answers() -> set[str]:
    out = set()
    for path in (PROPOSALS / "scripted").rglob("*.json"):
        obj = json.loads(path.read_text())
        for st in obj.get("stages", []) if isinstance(obj, dict) else []:
            out.update(st.get("sql", []))
            if len(st.get("description", "")) > 30:
                out.add(st["description"])
        for key in ("summary", "write_consistency", "revision_notes", "rationale"):
            if isinstance(obj, dict) and len(str(obj.get(key) or "")) > 30:
                out.add(obj[key])
        for case in obj.get("custom_cases", []) if isinstance(obj, dict) else []:
            out.add(case["id"])
            out.update(s["value"] for s in case["steps"] if s.get("value"))
        for finding in obj.get("findings", []) if isinstance(obj, dict) else []:
            if len(finding.get("reason", "")) > 30:
                out.add(finding["reason"])
    return out


def _verdict_labels() -> set[str]:
    out = set()
    for path in (PROPOSALS / "manual" / "scenarios").glob("*.json"):
        label = json.loads(path.read_text())
        out.add(label["note"])
        for name, expected in label["plans"].items():
            out.add(expected["why"])
            out.add(f'"verdict": "{expected["verdict"]}"')
            out.add(f"{label['scenario']}/{name}")
    return out


FORBIDDEN_PATHS = ["proposals/scripted", "proposals/manual", "patches/", "examples/",
                   "heldout/", "ground_truth"]


class CapturingLLM:
    model = "capture"
    base_url = "http://capture/v1"
    temperature = 1.0
    top_p = 1.0
    reasoning_enabled = False
    force_nonempty_content = False

    def __init__(self, variant: str):
        self.budget = ReviewBudget()
        self.calls: list[str] = []
        self.migration = "state_only" if variant == "state_only" else "apply"

    def serving_metadata(self):
        return {}

    def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                  history=None):
        self.calls += [system, user]
        if response_schema and response_schema["name"] == "migration_plan":
            obj = {"plan_id": "p", "recommendation": "staged_rollout", "stages": [
                {"id": "e", "kind": "sql", "phase": "expand", "sql": [FAKE_SQL]},
                {"id": "d", "kind": "deploy_new", "migration": self.migration},
                {"id": "r", "kind": "retire_old"},
                {"id": "c", "kind": "sql", "phase": "contract", "sql": [FAKE_SQL]}]}
            return validator(obj) if validator else obj
        if response_schema and response_schema["name"] == "tester_proposal":
            return {"checks": [], "custom_cases": []}
        if "Summarize" in user:
            return {"summary": "fake summary", "suspected_cause": "fake cause"}
        return {"findings": [], "implicit_paths": []}


@pytest.mark.parametrize("topology", ["roles", "single"])
@pytest.mark.parametrize("scenario_id", sorted(scenarios.SCENARIOS))
def test_rendered_prompts_contain_no_answers(monkeypatch, tmp_path, scenario_id, topology):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [
        {"ref": "saleor/account/models.py:1", "line": "x", "context": "x = 1"}])

    def fake_run(log, plan, tests, provenance, keep_db=False, scenario=None):
        assert scenario == scenario_id
        assert "recommendation" not in plan       # agent-side field never reaches the runner
        log.check("runner-check", False, provenance="runner")
        return {"ok": False, "plan_id": plan["plan_id"], "halted": "fake", "stages": [],
                "failed_checks": [], "recommendation": "reject"}

    monkeypatch.setattr(staged, "run", fake_run)
    sc = scenarios.get(scenario_id)
    llm = CapturingLLM(next(iter(sc.variants)))
    result = loop.run_review(RunLog("t", root=tmp_path), "llm", llm=llm,
                             topology=topology, scenario=scenario_id)
    assert result["scenario"] == scenario_id
    assert len(result["attempts"]) == 2, result["reason"]   # the revision prompt was rendered
    rendered = "\n".join(llm.calls)
    for needle in FORBIDDEN_PATHS:
        assert needle not in rendered, needle
    for answer in _scripted_answers() | _verdict_labels():
        assert answer not in rendered, answer[:80]


def test_answer_corpus_is_not_empty():
    assert len(_scripted_answers()) > 40
    assert len(_verdict_labels()) > 40


def test_non_rename_contract_names_scenario_checks():
    sc = scenarios.get("add-required-column")
    text = facts.contract_text(sc)
    assert "marketing_opt_in" in text and "state_only" in text
    assert "retire_old" in text and "repeat_until_zero_rows" in text
    assert "exactly as written" in text
    only_apply = facts.contract_text(scenarios.get("add-table"))
    assert 'must set "migration": "apply"' in only_apply
