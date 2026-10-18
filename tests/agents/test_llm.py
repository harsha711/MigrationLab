import json

import pytest
import requests

from migproto.agents.budget import ReviewBudget
from migproto.agents.llm import LLM, LLMError


class Response:
    def __init__(self, content="{}", status=200, usage=None, reasoning=None):
        self.status_code = status
        self.text = content
        self._body = {
            "choices": [{"message": {
                "content": content,
                "reasoning_content": reasoning,
            }}],
            "usage": usage,
        }

    def json(self):
        return self._body


def test_live_model_must_be_explicit(monkeypatch):
    monkeypatch.delenv("MIGPROTO_LLM_MODEL", raising=False)
    with pytest.raises(LLMError, match="required"):
        LLM()


def test_structured_output_is_validated_and_retried(monkeypatch):
    responses = iter([
        Response('{"wrong": true}', usage={"total_tokens": 2}),
        Response('{"ok": true}', usage={"prompt_tokens": 2, "completion_tokens": 1}),
    ])
    payloads = []

    def post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return next(responses)

    monkeypatch.setattr(requests, "post", post)
    budget = ReviewBudget(max_model_calls=3, max_total_tokens=20)
    llm = LLM(model="nemotron-test", budget=budget)

    def validate(obj):
        if obj != {"ok": True}:
            raise ValueError("missing ok")
        return obj

    transcript = []
    result = llm.chat_json("system", "user", transcript, validator=validate,
                           response_schema={"name": "x", "schema": {"type": "object"}})
    assert result == {"ok": True}
    assert budget.model_calls == 2
    assert budget.total_tokens == 5
    assert [entry["schema_valid"] for entry in transcript] == [False, True]
    assert payloads[0]["temperature"] == 1.0
    assert payloads[0]["top_p"] == 0.95
    assert payloads[0]["response_format"]["type"] == "json_schema"


def test_reasoning_without_content_is_not_accepted(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(
        "", usage={"total_tokens": 1}, reasoning="private reasoning"
    ))
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=3))
    transcript = []
    with pytest.raises(LLMError, match="empty message.content"):
        llm.chat_json("system", "user", transcript)
    assert len(transcript) == 3
    assert all(not entry["schema_valid"] for entry in transcript)


def test_request_failure_is_recorded(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout("late")

    monkeypatch.setattr(requests, "post", fail)
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=3))
    transcript = []
    with pytest.raises(LLMError, match="request failed"):
        llm.chat_json("system", "user", transcript)
    assert len(transcript) == 3
    assert all(entry["usage"] is None for entry in transcript)


def test_request_failure_retries_the_same_messages(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.Timeout("late")
        return Response('{"ok": true}', usage={"total_tokens": 1})

    monkeypatch.setattr(requests, "post", post)
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=3))
    assert llm.chat_json("system", "user", []) == {"ok": True}
    assert calls[1]["json"]["messages"] == calls[0]["json"]["messages"]


def test_request_timeout_is_capped_by_remaining_wall_budget(monkeypatch):
    timeouts = []

    def post(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return Response('{"ok": true}', usage={"total_tokens": 1})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("MIGPROTO_LLM_TIMEOUT", "120")
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_wall_seconds=30))
    llm.chat_json("system", "user", [])
    assert llm.timeout == 120
    assert 1.0 <= timeouts[0] <= 30


def test_ollama_reasoning_field_is_recorded(monkeypatch):
    response = Response('{"ok": true}', usage={"total_tokens": 1})
    message = response._body["choices"][0]["message"]
    del message["reasoning_content"]
    message["reasoning"] = "thinking"
    monkeypatch.setattr(requests, "post", lambda *a, **k: response)
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    transcript = []
    llm.chat_json("system", "user", transcript)
    assert transcript[0]["reasoning_content"] == "thinking"


def test_thinking_budget_is_sent_and_recorded(monkeypatch):
    payloads = []

    def post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return Response('{"ok": true}', usage={"total_tokens": 1})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("MIGPROTO_LLM_THINKING_BUDGET", "4096")
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    llm.chat_json("system", "user", [])
    assert payloads[0]["thinking_token_budget"] == 4096
    assert llm.serving_metadata()["thinking_token_budget"] == 4096

    monkeypatch.delenv("MIGPROTO_LLM_THINKING_BUDGET")
    payloads.clear()
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    llm.chat_json("system", "user", [])
    assert "thinking_token_budget" not in payloads[0]
    assert llm.serving_metadata()["thinking_token_budget"] is None


def test_advisory_precheck_retries_then_lets_the_last_reply_through(monkeypatch):
    from migproto import plans
    from migproto.agents.facts import PreCheckError

    def run(replies, retries=2):
        it = iter(Response(r) for r in replies)
        monkeypatch.setattr(requests, "post", lambda *a, **k: next(it))
        llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=5))

        def validate(obj):
            if obj.get("hard"):
                raise plans.PlanError("hard error")
            if obj.get("pre"):
                raise PreCheckError("advisory miss")
            return obj

        transcript = []
        return llm.chat_json("s", "u", transcript, retries=retries, validator=validate), \
            transcript

    # Corrected within the retries: the advisory error was sent back like any other.
    obj, t = run(['{"pre": 1}', '{"ok": 1}'])
    assert obj == {"ok": 1} and [e["schema_valid"] for e in t] == [False, True]
    # Still not met on the final retry: the plan goes through, and the miss is recorded.
    obj, t = run(['{"pre": 1}', '{"pre": 2}', '{"pre": 3}'])
    assert obj == {"pre": 3} and t[-1]["schema_valid"] and "advisory" in t[-1]["error"]
    # A hard error on the final retry still fails the call.
    with pytest.raises(LLMError, match="hard error"):
        run(['{"pre": 1}', '{"pre": 2}', '{"hard": 1}'])


def test_plan_validator_runs_hard_checks_before_prechecks():
    from migproto import plans
    from migproto.agents import facts
    sc = facts.for_scenario(None, [])
    validate = facts.plan_validator(sc)
    plan = {"plan_id": "p", "stages": [
        {"id": "e", "kind": "sql", "phase": "expand",
         "sql": ["UPDATE account_user SET customer_note = note LIMIT 5"]},
        {"id": "d", "kind": "deploy_new", "migration": "state_only", "variant": "nope"},
        {"id": "r", "kind": "retire_old"},
        {"id": "c", "kind": "sql", "phase": "contract", "sql": ["COMMENT ON TABLE account_user IS 'fixture'"]}]}
    with pytest.raises(plans.PlanError) as err:
        validate(plan)
    assert not isinstance(err.value, facts.PreCheckError)       # the variant error wins
    del plan["stages"][1]["variant"]
    with pytest.raises(facts.PreCheckError, match="syntax"):
        validate(plan)
