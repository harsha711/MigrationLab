import contextlib

import requests

from migproto.agents import tracing
from migproto.agents.budget import ReviewBudget
from migproto.agents.llm import LLM



class Response:
    def __init__(self, content="{}", usage=None, reasoning=None):
        self.status_code, self.text = 200, content
        self._body = {"choices": [{"message": {"content": content,
                                               "reasoning_content": reasoning}}],
                      "usage": usage}

    def json(self):
        return self._body


class FakeObs:
    def __init__(self, record):
        self.record = record

    def update(self, **fields):
        self.record.update(fields)


class FakeClient:
    def __init__(self, fail=False):
        self.observations, self.flushed, self.fail = [], 0, fail

    @contextlib.contextmanager
    def start_as_current_observation(self, **kwargs):
        if self.fail:
            raise RuntimeError("langfuse down")
        record = dict(kwargs)
        self.observations.append(record)
        yield FakeObs(record)

    def flush(self):
        self.flushed += 1


def _enable(monkeypatch, client):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(tracing, "_client", client)


def test_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(tracing, "_client", None)
    with tracing.generation("planner-1", "m", {}, []) as gen:
        assert gen is None
    assert tracing._client is None  # the SDK was never loaded


def test_generation_records_role_output_and_usage(monkeypatch):
    client = FakeClient()
    _enable(monkeypatch, client)
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(
        '{"ok": true}', usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        reasoning="thinking"))
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    llm.trace_name = "planner-1"
    assert llm.chat_json("system", "user", []) == {"ok": True}
    [gen] = client.observations
    assert gen["name"] == "planner-1" and gen["as_type"] == "generation"
    assert gen["usage_details"] == {"input": 7, "output": 3}
    assert gen["output"] == '{"ok": true}' and gen["level"] == "DEFAULT"
    assert gen["metadata"]["reasoning"] == "thinking"


def test_tracing_failure_never_breaks_a_call(monkeypatch):
    _enable(monkeypatch, FakeClient(fail=True))
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(
        '{"ok": true}', usage={"total_tokens": 1}))
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    assert llm.chat_json("system", "user", []) == {"ok": True}


def test_invalid_reply_is_an_error_generation(monkeypatch):
    client = FakeClient()
    _enable(monkeypatch, client)
    replies = iter([Response("not json", usage={"total_tokens": 1}),
                    Response('{"ok": true}', usage={"total_tokens": 1})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(replies))
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    llm.chat_json("system", "user", [])
    assert [g["level"] for g in client.observations] == ["ERROR", "DEFAULT"]
