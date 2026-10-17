import contextlib
import json
import uuid

import pytest
import requests

from migproto import staged
from migproto.agents import loop, roles, tracing
from migproto.agents.budget import BudgetExceeded, ReviewBudget
from migproto.agents.llm import LLM
from migproto.runlog import RunLog


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


def test_valid_reply_then_budget_exhausted_is_a_warning(monkeypatch):
    client = FakeClient()
    _enable(monkeypatch, client)
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(
        '{"ok": true}', usage={"prompt_tokens": 90, "completion_tokens": 20}))
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_total_tokens=100))
    with pytest.raises(BudgetExceeded):
        llm.chat_json("system", "user", [])
    [gen] = client.observations
    assert gen["level"] == "WARNING" and "token budget" in gen["status_message"]


class ExitFails(FakeClient):
    @contextlib.contextmanager
    def start_as_current_observation(self, **kwargs):
        yield FakeObs(dict(kwargs))
        raise RuntimeError("export broke on exit")


def test_tracing_failure_on_exit_never_breaks_a_call(monkeypatch):
    _enable(monkeypatch, ExitFails())
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response('{"ok": true}'))
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    assert llm.chat_json("system", "user", []) == {"ok": True}


# Real SDK (langfuse 5.0.2) with an in-memory exporter: nothing leaves the process.

@pytest.fixture
def sdk(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    key = f"pk-lf-unit-{uuid.uuid4().hex}"
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", key)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-unit")
    exporter = InMemorySpanExporter()
    client = Langfuse(public_key=key, secret_key="sk-lf-unit", base_url="http://127.0.0.1:9",
                      span_exporter=exporter, tracer_provider=TracerProvider())
    monkeypatch.setattr(client, "get_trace_url",
                        lambda trace_id=None: f"https://lf.test/traces/{trace_id}")
    monkeypatch.setattr(tracing, "_client", client)

    def spans():
        client.flush()
        return {s.name: s for s in exporter.get_finished_spans()}
    return spans


def _runner(ok_sequence):
    oks = iter(ok_sequence)

    def fake_staged(log, plan, tests, provenance, keep_db=False):
        ok = next(oks)
        log.check("runner-check", ok, provenance="runner", msg="unit")
        return {"ok": ok, "plan_id": plan["plan_id"], "halted": None if ok else "failed",
                "stages": [], "failed_checks": [] if ok else [{"name": "runner-check"}]}
    return fake_staged


def test_review_trace_carries_config_verdict_and_link(monkeypatch, tmp_path, sdk):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    monkeypatch.setattr(staged, "run", _runner([False, True]))
    request = {"change": {"description": "rename note"}, "agent_topology": "single",
               "request_id": "unit-request"}
    log = RunLog("test", root=tmp_path)
    assert loop.run_review(log, "scripted", request=request)["verdict"] == "PASS"
    spans = sdk()
    root = spans["review"]
    attrs = root.attributes
    assert root.parent is None and attrs["session.id"] == log.run_id
    assert attrs["langfuse.trace.name"] == "review single"  # resolved from the request
    assert set(attrs["langfuse.trace.tags"]) == {
        "source:scripted", "topology:single", "verdict:PASS"}
    assert attrs["langfuse.trace.metadata.request_id"] == '"unit-request"'
    assert attrs["langfuse.trace.metadata.max_revisions"] == "1"
    assert attrs["langfuse.trace.metadata.thinking_budget"] == '"unset"'
    for name in ("runner attempt 1", "runner attempt 2"):
        assert spans[name].parent.span_id == root.context.span_id
        assert spans[name].attributes["session.id"] == log.run_id
    assert spans["runner attempt 1"].attributes["langfuse.observation.level"] == "WARNING"
    link = json.loads((log.dir / "agents" / "trace.json").read_text())
    trace_id = format(root.context.trace_id, "032x")
    assert link["trace_id"] == trace_id and link["url"].endswith(trace_id)
    assert not (log.dir / "summary.json").exists()  # run_review never writes summary.json


def test_incomplete_review_ends_its_trace(monkeypatch, tmp_path, sdk):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])
    llm = LLM(model="nemotron-test", budget=ReviewBudget(max_model_calls=0))
    llm.thinking_budget = 4096
    assert loop.run_review(RunLog("test", root=tmp_path), "llm", llm=llm)["verdict"] == \
        "INCOMPLETE"
    root = sdk()["review"]
    assert "verdict:INCOMPLETE" in root.attributes["langfuse.trace.tags"]
    assert root.attributes["langfuse.trace.metadata.model"] == '"nemotron-test"'
    assert root.attributes["langfuse.trace.metadata.thinking_budget"] == "4096"
    assert "model-call budget" in root.attributes["langfuse.observation.status_message"]


def test_controller_exception_ends_trace_and_propagates(monkeypatch, tmp_path, sdk):
    monkeypatch.setattr(roles, "gather_hits", lambda: [])

    def broken(*args, **kwargs):
        raise RuntimeError("runner crashed")
    monkeypatch.setattr(staged, "run", broken)
    log = RunLog("test", root=tmp_path)
    with pytest.raises(RuntimeError, match="runner crashed"):
        loop.run_review(log, "scripted")
    root = sdk()["review"]
    assert root.attributes["langfuse.observation.level"] == "ERROR"
    assert "verdict:EXCEPTION" in root.attributes["langfuse.trace.tags"]
    assert (log.dir / "agents" / "trace.json").exists()


def test_single_topology_one_generation_per_call_without_duplicated_history(
        monkeypatch, tmp_path, sdk):
    replies = iter([Response('{"step": 1}', usage={"prompt_tokens": 5, "completion_tokens": 2}),
                    Response('{"step": 2}', usage={"prompt_tokens": 9, "completion_tokens": 2})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(replies))
    llm = LLM(model="nemotron-test", budget=ReviewBudget())
    history: list[dict] = []
    with tracing.review(RunLog("test", root=tmp_path), "llm"):
        for role in ("investigator", "planner-1"):
            llm.trace_name = role
            llm.chat_json("single system", f"{role} request", [], history=history)
    spans = sdk()
    gens = [spans["investigator"], spans["planner-1"]]
    assert all(g.parent.span_id == spans["review"].context.span_id for g in gens)
    # Each input is the history as sent at that call, not the list as it ended up.
    assert [len(json.loads(g.attributes["langfuse.observation.input"])) for g in gens] == [2, 4]
    for g in gens:
        assert not any(k.startswith("langfuse.observation.metadata.") and "messages" in k
                       for k in g.attributes)
        assert "messages" not in g.attributes.get("langfuse.observation.metadata", "")
