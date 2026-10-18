"""gym/eval_policy.py against a local fake endpoint (no model, no GPU); staged.run mocked."""

import hashlib
import json

import pytest

import eval_policy
from migproto import staged
from migration_review import env, scoring, tasks
from test_verify import FakeRunner

LLM_ENV = ("MIGPROTO_LLM_BASE_URL", "MIGPROTO_LLM_MODEL", "MIGPROTO_LLM_API_KEY",
           "MIGPROTO_LLM_THINKING_BUDGET", "MIGPROTO_LLM_REASONING",
           "MIGPROTO_LLM_TEMPERATURE", "MIGPROTO_LLM_TOP_P", "MIGPROTO_LLM_FORCE_NONEMPTY")


@pytest.fixture
def llm_env(monkeypatch):
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)

    def set_endpoint(fake, **extra):
        monkeypatch.setenv("MIGPROTO_LLM_BASE_URL", fake.base_url)
        monkeypatch.setenv("MIGPROTO_LLM_MODEL", "fake-model")
        monkeypatch.setenv("MIGPROTO_LLM_API_KEY", "sk-test-secret")
        for k, v in extra.items():
            monkeypatch.setenv(k, v)
    return set_endpoint


@pytest.fixture
def runner(monkeypatch, tmp_path):
    fake = FakeRunner()
    monkeypatch.setattr(staged, "run", fake)
    real = scoring.verify
    monkeypatch.setattr(scoring, "verify", lambda sid, text, model=None: real(
        sid, text, model=model, runs_root=tmp_path / "runs"))
    return fake


def _lines(out):
    return [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]


def test_evaluates_selected_tasks_and_writes_results(tmp_path, llm_env, runner):
    with eval_policy.FakeEndpoint() as fake:
        llm_env(fake, MIGPROTO_LLM_THINKING_BUDGET="4096")
        out = eval_policy.evaluate(tasks.TASKS_FILE, "add-table,migproto-drop-column",
                                   eval_policy.settings_from_env(), "schema", 0,
                                   out_root=tmp_path / "results")
    rows = _lines(out)
    assert [r["task_id"] for r in rows] == ["migproto-add-table", "migproto-drop-column"]
    add, drop = rows
    assert add["reward"] == 1.0 and add["status"] == "runner_pass_on_target"
    assert add["run_id"] and add["failed_checks"] == []
    assert add["tokens"] == {"prompt": 100, "completion": 20, "total": 120}
    assert add["latency_s"] is not None and add["model_attempts"] == 1
    # The fake's direct deploy passes the mocked runner, but drop-column's target is staged.
    assert drop["reward"] == 0.0 and drop["status"] == "runner_recommendation_off_target"
    assert len(runner.calls) == 2                       # one runner call per task, in order

    # Requests: task messages verbatim, llm.py settings, guided-decoding schema.
    frozen = {r["task_id"]: r for r in tasks.load()}
    req = fake.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer sk-test-secret"
    body = req["body"]
    assert body["messages"] == frozen["migproto-add-table"]["responses_create_params"]["input"]
    assert body["model"] == "fake-model" and body["thinking_token_budget"] == 4096
    assert body["temperature"] == 1.0 and body["top_p"] == 0.95
    assert body["chat_template_kwargs"] == {"force_nonempty_content": True,
                                            "enable_thinking": True}
    schema = body["response_format"]["json_schema"]
    assert schema["name"] == "migration_plan"
    deploy = schema["schema"]["properties"]["stages"]["items"]["anyOf"][1]
    assert deploy["properties"]["migration"] == {"enum": ["apply"]}   # add-table: apply only

    config = json.loads((out / "config.json").read_text())
    assert config["model"] == "fake-model" and config["endpoint"] == fake.base_url
    assert config["thinking_token_budget"] == 4096
    assert config["sampling"]["temperature"] == 1.0
    assert config["task_file_sha256"] == hashlib.sha256(tasks.TASKS_FILE.read_bytes()).hexdigest()
    assert config["tasks"] == ["migproto-add-table", "migproto-drop-column"]
    assert "nemo_gym_version" in config and config["dry_run"] is False
    assert config["runner_env"] == env.RUNNER_ENV
    assert "sk-test-secret" not in (out / "config.json").read_text()
    for answer in (out / "answers").iterdir():
        assert "sk-test-secret" not in answer.read_text()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["tasks"] == 2 and summary["reward_sum"] == 1.0


def test_no_thinking_budget_or_reasoning_off_sends_none(tmp_path, llm_env, runner):
    with eval_policy.FakeEndpoint() as fake:
        llm_env(fake, MIGPROTO_LLM_REASONING="false", MIGPROTO_LLM_THINKING_BUDGET="4096")
        eval_policy.evaluate(tasks.TASKS_FILE, "add-table", eval_policy.settings_from_env(),
                             "json_object", 0, out_root=tmp_path)
    body = fake.requests[0]["body"]
    assert "thinking_token_budget" not in body
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert body["response_format"] == {"type": "json_object"}


def test_endpoint_failure_is_masked_model_error(tmp_path, llm_env, runner):
    with eval_policy.FakeEndpoint(status=500) as fake:
        llm_env(fake)
        out = eval_policy.evaluate(tasks.TASKS_FILE, "add-table",
                                   eval_policy.settings_from_env(), "schema", 2,
                                   out_root=tmp_path)
    (row,) = _lines(out)
    assert row["status"] == "model_error" and row["mask_sample"] is True
    assert row["reward"] == 0.0 and row["run_id"] is None and "HTTP 500" in row["error"]
    assert row["model_attempts"] == 3 and len(fake.requests) == 3
    assert not runner.calls                              # nothing reached the runner
    assert json.loads((out / "summary.json").read_text())["masked"] == 1


def test_non_plan_answer_is_scored_without_runner(tmp_path, llm_env, runner):
    with eval_policy.FakeEndpoint(answer="I would deploy it directly.") as fake:
        llm_env(fake)
        out = eval_policy.evaluate(tasks.TASKS_FILE, "add-table",
                                   eval_policy.settings_from_env(), "none", 0,
                                   out_root=tmp_path)
    (row,) = _lines(out)
    assert row["status"] == "no_plan_json" and row["mask_sample"] is False
    assert "response_format" not in fake.requests[0]["body"]
    assert not runner.calls


def test_unknown_task_is_refused():
    with pytest.raises(SystemExit, match="unknown tasks"):
        eval_policy.select(tasks.load(), "add-table,nope")


def test_dry_run_main(tmp_path, monkeypatch, runner):
    for name in LLM_ENV:   # set, then delete: monkeypatch restores what main() writes
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    applied = []
    monkeypatch.setattr(env, "apply", lambda: applied.append(True))   # keep test env clean
    assert eval_policy.main(["--dry-run", "--tasks", "add-table", "--out", str(tmp_path / "results")]) == 0
    assert applied
    (out,) = (tmp_path / "results").iterdir()
    assert out.name.endswith("-dry-run")
    config = json.loads((out / "config.json").read_text())
    assert config["dry_run"] is True and config["model"] == eval_policy.FAKE_MODEL
    assert config["endpoint"].startswith("http://127.0.0.1:")
    (row,) = _lines(out)
    assert row["reward"] == 1.0 and runner.calls[0]["scenario"] == "add-table"
