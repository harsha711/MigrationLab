"""Server tests; run in a NeMo Gym venv (nemo-gym 0.7.0), not in the MigrationLab suite:

    cd gym/resources_server/migproto_review && <nemo-gym venv>/bin/python -m pytest tests -q

The bridge test runs the real gym/verify_cli.py through `uv run` on answers that never reach
the runner (no plan, invalid plan), so it starts no Saleor server and needs no Docker.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nemo_gym.server_utils import ServerClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]


def _server(**kw) -> app.MigprotoReviewResourcesServer:
    config = app.MigprotoReviewResourcesServerConfig(
        host="127.0.0.1", port=8090, entrypoint="", name="migproto_review",
        migproto_root=str(ROOT), **kw)
    return app.MigprotoReviewResourcesServer(config=config,
                                             server_client=MagicMock(spec=ServerClient))


def _request(text: str, scenario_id: str = "add-table") -> app.MigprotoReviewVerifyRequest:
    return app.MigprotoReviewVerifyRequest.model_validate({
        "responses_create_params": {"input": "prompt"},
        "scenario_id": scenario_id, "task_id": f"migproto-{scenario_id}",
        "response": {
            "id": "r", "created_at": 0, "model": "fixture-model", "object": "response",
            "output": [{"id": "m", "type": "message", "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text,
                                     "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": []},
    })


def test_verify_forwards_answer_and_returns_runner_info(monkeypatch):
    seen = {}

    async def fake(config, payload):
        seen.update(payload)
        return {"reward": 1.0, "status": "runner_pass_on_target", "run_id": "r1",
                "runner_verdict": "pass", "runner_recommendation": "accept_as_is",
                "failed_checks": [], "mask_sample": False}

    monkeypatch.setattr(app, "run_verifier", fake)
    out = asyncio.run(_server().verify(_request('{"plan_id": "p"}')))
    assert seen == {"scenario_id": "add-table", "output_text": '{"plan_id": "p"}',
                    "model": "fixture-model"}
    assert out.reward == 1.0 and out.run_id == "r1" and out.mask_sample is False
    assert out.runner_verdict == "pass" and out.scenario_id == "add-table"


def test_runner_failure_names_are_returned(monkeypatch):
    async def fake(config, payload):
        return {"reward": 0.0, "status": "runner_did_not_pass", "run_id": "r2",
                "runner_verdict": "fail", "failed_checks": ["case.old_write_new_read"]}

    monkeypatch.setattr(app, "run_verifier", fake)
    out = asyncio.run(_server().verify(_request("{}")))
    assert out.reward == 0.0 and out.failed_checks == ["case.old_write_new_read"]
    assert out.mask_sample is False


def test_verifier_crash_is_masked(monkeypatch):
    async def boom(config, payload):
        raise RuntimeError("verify_cli exited 1")

    monkeypatch.setattr(app, "run_verifier", boom)
    out = asyncio.run(_server().verify(_request("{}")))
    assert out.reward == 0.0 and out.mask_sample is True
    assert out.failure_kind == "verifier_error" and "exited 1" in out.failure_reason


@pytest.mark.parametrize("text,status", [
    ("I would rename the column.", "no_plan_json"),
    (json.dumps({"plan_id": "p", "recommendation": "accept_as_is", "stages": []}),
     "invalid_plan"),
])
def test_real_bridge_without_runner(text, status):
    out = asyncio.run(_server(verify_timeout_s=120).verify(_request(text)))
    assert out.status == status, out
    assert out.reward == 0.0 and out.mask_sample is False and out.run_id is None


def test_http_verify_route(monkeypatch):
    from fastapi.testclient import TestClient

    async def fake(config, payload):
        return {"reward": 1.0, "status": "runner_pass_on_target", "run_id": "r3"}

    monkeypatch.setattr(app, "run_verifier", fake)
    client = TestClient(_server().setup_webserver())
    body = _request("{}").model_dump(mode="json")
    resp = client.post("/verify", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reward"] == 1.0 and data["run_id"] == "r3" and data["scenario_id"] == "add-table"
    assert client.get("/reverify_mode").json() == "stateless"
