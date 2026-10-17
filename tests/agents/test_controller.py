import json

from migproto.agents.budget import ReviewBudget
from migproto.agents.llm import LLM
from migproto.agents import loop, roles
from migproto.runlog import RunLog


def test_scripted_revision_preserves_both_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    calls = []

    def fake_staged(log, plan, tests, provenance):
        calls.append(plan["plan_id"])
        ok = len(calls) == 2
        log.check("runner-check", ok, provenance="runner", msg="complete evidence")
        return {
            "ok": ok,
            "plan_id": plan["plan_id"],
            "halted": None if ok else "first attempt failed",
            "stages": [],
            "failed_checks": [] if ok else [{"name": "runner-check"}],
        }

    monkeypatch.setattr(loop.staged, "run", fake_staged)
    log = RunLog("test", root=tmp_path)
    result = loop.run_review(log, "scripted")
    assert result["verdict"] == "PASS"
    assert [attempt["ok"] for attempt in result["attempts"]] == [False, True]
    assert result["revision_fixed_failures"] is True
    assert result["attempts"][0]["failed_checks"] == ["runner-check"]
    events = [json.loads(line) for line in (log.dir / "events.jsonl").read_text().splitlines()]
    claim = next(event for event in events if event["kind"] == "agent_claim")
    assert "/a1/runner-check#" in claim["evidence"][0]


def test_budget_and_topology_are_shared_controller_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    monkeypatch.setattr(loop.staged, "run", lambda log, plan, tests, provenance: {
        "ok": True, "plan_id": plan["plan_id"], "halted": None,
        "stages": [], "failed_checks": [],
    })
    request = {
        "change": {"description": "rename note"},
        "agent_topology": "single",
        "budget": {"max_revisions": 0, "max_model_calls": 4,
                   "max_total_tokens": 100, "max_wall_seconds": 30},
    }
    result = loop.run_review(RunLog("test", root=tmp_path), "scripted", request=request)
    assert result["topology"] == "single"
    assert result["agent_totals"]["max_model_calls"] == 4
    assert result["agent_totals"]["max_total_tokens"] == 100


def test_rendered_role_prompts_do_not_name_restricted_answers():
    forbidden = [
        "proposals/scripted", "proposals/manual", "patches/", "examples/",
        "heldout/", "migproto_note_sync",
    ]
    prompt_sources = [
        roles.SCENARIO, roles.PLAN_CONTRACT, roles.INVESTIGATOR_SYSTEM,
        roles.PLANNER_SYSTEM, roles.TESTER_SYSTEM,
    ]
    rendered = "\n".join(prompt_sources)
    assert not any(value in rendered for value in forbidden)


def test_call_budget_exhaustion_returns_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=0))
    result = loop.run_review(RunLog("test", root=tmp_path), "llm", llm=llm)
    assert result["verdict"] == "INCOMPLETE"
    assert "model-call budget" in result["reason"]


def test_invalid_plan_never_reaches_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    executed = []
    monkeypatch.setattr(loop.staged, "run", lambda *args, **kwargs: executed.append(True))

    class FakeLLM:
        model = "nemotron-test"
        base_url = "http://test/v1"
        temperature = 1.0
        top_p = 0.95
        reasoning_enabled = True
        force_nonempty_content = True
        budget = ReviewBudget()
        replies = iter([{"findings": [], "implicit_paths": []},
                        {"plan_id": "invalid", "stages": []}])

        def serving_metadata(self):
            return {"serving_backend": "vllm"}

        def chat_json(self, *args, **kwargs):
            return next(self.replies)

    result = loop.run_review(RunLog("test", root=tmp_path), "llm", llm=FakeLLM())
    assert result["verdict"] == "ERROR"
    assert "invalid controller input or proposal" in result["reason"]
    assert executed == []


def test_missing_live_model_returns_error(monkeypatch, tmp_path):
    monkeypatch.delenv("MIGPROTO_LLM_MODEL", raising=False)
    result = loop.run_review(RunLog("test", root=tmp_path), "llm")
    assert result["verdict"] == "ERROR"
    assert "MIGPROTO_LLM_MODEL is required" in result["reason"]
