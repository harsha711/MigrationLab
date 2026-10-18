import json

from migproto.agents.budget import ReviewBudget
from migproto.agents.llm import LLM
from migproto import staged
from migproto.agents import loop, roles
from migproto.runlog import RunLog


def test_scripted_revision_preserves_both_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    calls = []

    def fake_staged(log, plan, tests, provenance, keep_db=False, scenario=None):
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

    monkeypatch.setattr(staged, "run", fake_staged)
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
    monkeypatch.setattr(staged, "run", lambda log, plan, tests, provenance, keep_db=False, scenario=None: {
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
    monkeypatch.setattr(staged, "run", lambda *args, **kwargs: executed.append(True))

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


def test_scripted_mode_is_rename_only(tmp_path):
    result = loop.run_review(RunLog("test", root=tmp_path), "scripted",
                             scenario="add-required-column")
    assert result["verdict"] == "ERROR"
    assert "scripted agent proposals exist only for rename-note" in result["reason"]


def test_agreed_reject_stops_without_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    runs = []

    def fake_staged(log, plan, tests, provenance, keep_db=False, scenario=None):
        runs.append((scenario, plan))
        log.check("runner-check", False, provenance="runner")
        return {"ok": False, "plan_id": plan["plan_id"], "halted": None, "stages": [],
                "failed_checks": [], "recommendation": "reject"}

    monkeypatch.setattr(staged, "run", fake_staged)

    class FakeLLM:
        model = "nemotron-test"
        base_url = "http://test/v1"
        temperature = 1.0
        top_p = 0.95
        reasoning_enabled = True
        force_nonempty_content = True
        budget = ReviewBudget()

        def serving_metadata(self):
            return {}

        def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                      history=None):
            if response_schema and response_schema["name"] == "migration_plan":
                return validator({"plan_id": "direct", "recommendation": "reject", "stages": [
                    {"id": "d", "kind": "deploy_new", "migration": "apply"},
                    {"id": "r", "kind": "retire_old"}]})
            if response_schema:
                return {"checks": [], "custom_cases": []}
            return {"findings": [], "implicit_paths": [], "summary": "s"}

    result = loop.run_review(RunLog("test", root=tmp_path), "llm", llm=FakeLLM(),
                             scenario="unique-last-name")
    assert len(runs) == 1 and runs[0][0] == "unique-last-name"
    assert "recommendation" not in runs[0][1]
    assert result["verdict"] == "FAIL"
    assert result["recommendation"] == "reject" == result["runner_recommendation"]
    assert result["recommendation_agrees"] is True


def test_accept_as_is_must_match_plan_shape():
    from migproto import plans, scenarios
    from migproto.agents import facts
    import pytest
    validate = facts.plan_validator(facts.for_scenario("add-optional-column", roles.SEARCH_ROOTS))
    with pytest.raises(plans.PlanError, match="accept_as_is"):
        validate({"plan_id": "x", "recommendation": "accept_as_is", "stages": [
            {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
            {"id": "d", "kind": "deploy_new", "migration": "apply"},
            {"id": "r", "kind": "retire_old"}]})
    with pytest.raises(plans.PlanError, match="variant"):
        validate({"plan_id": "x", "stages": [   # omitted migration means state_only
            {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
            {"id": "d", "kind": "deploy_new"}, {"id": "r", "kind": "retire_old"},
            {"id": "c", "kind": "sql", "phase": "contract", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]}]})
    assert scenarios.get("add-optional-column").variants.keys() == {"apply"}


def test_scenario_plan_validator_rejects_variant_that_contradicts_migration():
    # Seen on Lightning (drop-column, backfill-then-required): "migration": "state_only" with
    # "variant": "apply" made the runner deploy the apply build.
    from migproto import plans
    from migproto.agents import facts
    import pytest

    def plan(**deploy):
        return {"plan_id": "x", "stages": [
            {"id": "e", "kind": "sql", "phase": "expand", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]},
            {"id": "d", "kind": "deploy_new", **deploy}, {"id": "r", "kind": "retire_old"},
            {"id": "c", "kind": "sql", "phase": "contract", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]}]}

    drop = facts.plan_validator(facts.for_scenario("drop-column", roles.SEARCH_ROOTS))
    with pytest.raises(plans.PlanError, match="leave \"variant\" out"):
        drop(plan(migration="state_only", variant="apply"))
    with pytest.raises(plans.PlanError, match="leave \"variant\" out"):
        drop(plan(migration="state_only", variant="state_only"))
    assert drop(plan(migration="state_only"))
    assert drop(plan(migration="apply"))

    b4 = facts.plan_validator(facts.for_scenario("irreversible-data-migration",
                                                 roles.SEARCH_ROOTS))
    assert b4(plan(migration="apply", variant="apply-reversible"))
    with pytest.raises(plans.PlanError, match="needs deploy_new"):
        b4(plan(variant="apply-reversible"))
    with pytest.raises(plans.PlanError, match="apply-reversible"):
        b4(plan(migration="apply", variant="apply"))


def test_parallel_planners_share_runner_facts_and_stop_on_first_pass(monkeypatch, tmp_path):
    import threading
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    runs = []

    def fake_staged(log, plan, tests, provenance, keep_db=False, scenario=None):
        runs.append(plan["plan_id"])
        ok = len(runs) == 5   # round 1: a, b, c fail; round 2: a fails, b passes
        log.check("runner-check", ok, provenance="runner")
        return {"ok": ok, "plan_id": plan["plan_id"], "halted": None, "stages": [],
                "failed_checks": [] if ok else [{"name": "runner-check"}]}

    monkeypatch.setattr(staged, "run", fake_staged)
    prompts, lock = [], threading.Lock()

    class FakeLLM:
        model = "nemotron-test"
        base_url = "http://test/v1"
        temperature = 1.0
        top_p = 0.95
        reasoning_enabled = True
        force_nonempty_content = True
        budget = ReviewBudget(max_revisions=3, max_model_calls=40)

        def serving_metadata(self):
            return {}

        def chat_json(self, system, user, transcript, validator=None, response_schema=None,
                      history=None):
            self.budget.begin_call()
            self.budget.finish_call({"total_tokens": 10}, 0.0, valid=True)
            if response_schema and response_schema["name"] == "migration_plan":
                with lock:
                    prompts.append((self.trace_name, user))
                return validator({"plan_id": "p", "stages": [
                    {"id": "e", "kind": "sql", "phase": "expand",
                     "sql": ["ALTER TABLE account_user ADD COLUMN customer_note text"]},
                    {"id": "d", "kind": "deploy_new"}, {"id": "r", "kind": "retire_old"}]})
            if response_schema:
                return {"checks": [], "custom_cases": []}
            return {"findings": [], "implicit_paths": []}

    llm = FakeLLM()
    result = loop.run_review(RunLog("test", root=tmp_path), "llm", llm=llm,
                             request={"change": {"description": "rename note"},
                                      "agent_topology": "parallel"})
    assert result["verdict"] == "PASS"
    assert [(a["round"], a["planner"]) for a in result["attempts"]] == [
        (1, "a"), (1, "b"), (1, "c"), (2, "a"), (2, "b")]
    assert [a["ok"] for a in result["attempts"]] == [False] * 4 + [True]
    assert runs == [f"a{n}-p" for n in range(1, 6)]
    # 1 investigator + 2 rounds x (3 planners + 3 testers), all on the one shared budget.
    assert result["agent_totals"]["model_calls"] == 13
    revisions = sorted(u for name, u in prompts if name.startswith("planner-2"))
    assert len(revisions) == 3
    assert all("another planner, same round" in u and "tester_hypothesis" not in u
               for u in revisions)
