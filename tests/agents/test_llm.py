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
