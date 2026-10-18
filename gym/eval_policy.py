"""Evaluate a policy model on the frozen Gym tasks, one task at a time.

    MIGPROTO_LLM_BASE_URL=http://localhost:8000/v1 MIGPROTO_LLM_MODEL=nemotron-3.5-lightning \
    MIGPROTO_LLM_THINKING_BUDGET=4096 uv run python gym/eval_policy.py [--tasks add-table,drop-column]

    uv run python gym/eval_policy.py --dry-run --tasks add-table   # local fake endpoint, no model

For each task: send its responses_create_params input messages to the OpenAI-compatible
endpoint (chat completions, with the sampling, chat_template_kwargs and thinking_token_budget
settings that migproto/agents/llm.py reads from MIGPROTO_LLM_*), then score the answer with
migration_review.scoring.verify (the deterministic runner). Writes gym/results/<timestamp>/:
config.json, results.jsonl (one line per task), answers/<task_id>.json (request settings and
the raw reply), summary.json.

Single-step like the tasks: one answer per task, no correction turn. Transport failures
(HTTP error, timeout) are retried up to --retries times and, if they persist, recorded with
status "model_error" and mask_sample true. This calls the endpoint directly, not through a
NeMo Gym agent or model server.

--dry-run starts a fake endpoint on 127.0.0.1 that answers every task with a direct-deploy
plan (deploy_new "apply", retire_old, recommendation accept_as_is). The runner still executes
it, so every selected task starts Saleor (ports 8081/8082, Redis DB 8, this checkout's lock).
"""

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path

import requests

from migration_review import env

GYM = Path(__file__).resolve().parent
RESULTS = GYM / "results"
FAKE_MODEL = "fake-direct-deploy"
FAKE_PLAN = {"plan_id": "fake-direct", "recommendation": "accept_as_is",
             "summary": "Dry-run answer from the fake endpoint: deploy directly.",
             "stages": [{"id": "deploy_new", "kind": "deploy_new", "migration": "apply"},
                        {"id": "retire_old", "kind": "retire_old"}]}


# --- fake endpoint (dry run and tests) ---------------------------------------------------------

class FakeEndpoint:
    """Minimal OpenAI-compatible chat-completions server on 127.0.0.1 (random port)."""

    def __init__(self, answer: str | None = None, status: int = 200):
        self.answer = json.dumps(FAKE_PLAN) if answer is None else answer
        self.status = status
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body,
                                       "auth": self.headers.get("Authorization")})
                if outer.status != 200:
                    return self._send(outer.status, {"error": "fake failure"})
                self._send(200, {
                    "id": "fake", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": outer.answer,
                                             "reasoning_content": "fake reasoning"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                              "total_tokens": 120}})

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


# --- model call --------------------------------------------------------------------------------

def settings_from_env() -> dict:
    """The request settings migproto/agents/llm.py would use (read through LLM itself)."""
    from migproto.agents.llm import LLM

    llm = LLM()
    return {"base_url": llm.base_url, "model": llm.model, "api_key": llm.api_key,
            "timeout_s": llm.timeout, "temperature": llm.temperature, "top_p": llm.top_p,
            "reasoning_enabled": llm.reasoning_enabled,
            "force_nonempty_content": llm.force_nonempty_content,
            "thinking_token_budget": llm.thinking_budget,
            "serving": llm.serving_metadata()}


def response_format(mode: str, scenario_id: str) -> dict | None:
    """'schema': the controller planner's guided-decoding schema for this scenario's builds;
    'json_object': plain JSON mode; 'none': no response_format."""
    if mode == "none":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    from migproto import scenarios
    from migproto.agents import roles

    variants = tuple(scenarios.get(scenario_id).variants)
    return {"type": "json_schema", "json_schema": roles.plan_schema(variants)}


def build_payload(task: dict, s: dict, fmt: dict | None) -> dict:
    payload = {
        "model": s["model"],
        "messages": task["responses_create_params"]["input"],
        "temperature": s["temperature"],
        "top_p": s["top_p"],
        "chat_template_kwargs": {"force_nonempty_content": s["force_nonempty_content"],
                                 "enable_thinking": s["reasoning_enabled"]},
    }
    if fmt is not None:
        payload["response_format"] = fmt
    if s["reasoning_enabled"] and s["thinking_token_budget"] is not None:
        payload["thinking_token_budget"] = s["thinking_token_budget"]
    return payload


def call_model(payload: dict, s: dict, retries: int) -> dict:
    """One answer. Retries transport failures only; never adds a correction turn."""
    errors = []
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        try:
            resp = requests.post(f"{s['base_url']}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {s['api_key']}"},
                                 timeout=s["timeout_s"])
            latency = round(time.monotonic() - t0, 3)
            if resp.status_code != 200:
                errors.append(f"HTTP {resp.status_code}: {resp.text[:300]}")
                continue
            body = resp.json()
            message = body["choices"][0]["message"]
            return {"ok": True, "content": message.get("content") or "",
                    "reasoning": message.get("reasoning_content") or message.get("reasoning"),
                    "finish_reason": body["choices"][0].get("finish_reason"),
                    "usage": body.get("usage"), "latency_s": latency,
                    "attempts": attempt + 1, "errors": errors}
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:300])
    return {"ok": False, "content": None, "reasoning": None, "finish_reason": None,
            "usage": None, "latency_s": None, "attempts": retries + 1, "errors": errors}


# --- run ---------------------------------------------------------------------------------------

def select(rows: list[dict], spec: str | None) -> list[dict]:
    if not spec:
        return rows
    wanted = [w.strip() for w in spec.split(",") if w.strip()]
    by_key = {r["scenario_id"]: r for r in rows} | {r["task_id"]: r for r in rows}
    unknown = [w for w in wanted if w not in by_key]
    if unknown:
        raise SystemExit(f"unknown tasks {unknown}; known: {sorted(r['scenario_id'] for r in rows)}")
    return list({by_key[w]["task_id"]: by_key[w] for w in wanted}.values())


def _nemo_gym_version() -> str:
    try:
        return metadata.version("nemo-gym")
    except metadata.PackageNotFoundError:
        return ("not installed in this venv (this script calls the endpoint directly; the tasks "
                "follow the nemo-gym 0.7.0 resources-server format, see gym/README.md)")


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=GYM, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def evaluate(task_file: Path, tasks_spec: str | None, s: dict, fmt_mode: str, retries: int,
             out_root: Path = RESULTS, dry_run: bool = False, verify=None) -> Path:
    from migration_review import scoring, tasks

    verify = verify or scoring.verify
    rows = select(tasks.load(task_file), tasks_spec)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-dry-run" if dry_run else "")
    out = out_root / stamp
    (out / "answers").mkdir(parents=True, exist_ok=False)
    config = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "model": s["model"], "endpoint": s["base_url"],
        "sampling": {"temperature": s["temperature"], "top_p": s["top_p"],
                     "reasoning_enabled": s["reasoning_enabled"],
                     "force_nonempty_content": s["force_nonempty_content"],
                     "response_format": fmt_mode, "max_tokens": None},
        "thinking_token_budget": s["thinking_token_budget"],
        "request_timeout_s": s["timeout_s"], "transport_retries": retries,
        "serving": s["serving"],
        "nemo_gym_version": _nemo_gym_version(),
        "task_file": str(task_file.relative_to(GYM.parent) if task_file.is_relative_to(GYM.parent)
                         else task_file),
        "task_file_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "tasks": [r["task_id"] for r in rows],
        "runner_env": {k: v for k, v in env.RUNNER_ENV.items()},
        "git_commit": _git_commit(),
        "workflow": "single-step: one chat completion per task, scored by the runner",
    }
    (out / "config.json").write_text(json.dumps(config, indent=2))
    results = []
    with (out / "results.jsonl").open("w") as fh:
        for row in rows:
            fmt = response_format(fmt_mode, row["scenario_id"])
            payload = build_payload(row, s, fmt)
            reply = call_model(payload, s, retries)
            (out / "answers" / f"{row['task_id']}.json").write_text(json.dumps(
                {"request": {k: v for k, v in payload.items() if k != "messages"},
                 "prompt_sha256": row.get("prompt_sha256"), **reply},
                indent=2, ensure_ascii=False))
            if reply["ok"]:
                scored = verify(row["scenario_id"], reply["content"], model=s["model"])
            else:
                scored = {"reward": 0.0, "status": "model_error", "mask_sample": True,
                          "run_id": None, "failed_checks": [],
                          "error": "; ".join(reply["errors"])[-500:]}
            usage = reply["usage"] or {}
            line = {
                "task_id": row["task_id"], "scenario_id": row["scenario_id"],
                "reward": scored["reward"], "status": scored["status"],
                "mask_sample": scored.get("mask_sample", False),
                "run_id": scored.get("run_id"), "failed_checks": scored.get("failed_checks", []),
                "tokens": {"prompt": usage.get("prompt_tokens"),
                           "completion": usage.get("completion_tokens"),
                           "total": usage.get("total_tokens")},
                "latency_s": reply["latency_s"], "model_attempts": reply["attempts"],
                "finish_reason": reply["finish_reason"],
                "policy_recommendation": scored.get("policy_recommendation"),
                "runner_verdict": scored.get("runner_verdict"),
                "runner_recommendation": scored.get("runner_recommendation"),
                "error": scored.get("error"),
            }
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(line)
            print(f"{row['task_id']}: reward={line['reward']} status={line['status']} "
                  f"run={line['run_id']}", flush=True)
    scored_rows = [r for r in results if not r["mask_sample"]]
    summary = {
        "tasks": len(results), "scored": len(scored_rows), "masked": len(results) - len(scored_rows),
        "reward_sum": sum(r["reward"] for r in scored_rows),
        "mean_reward_scored": (round(sum(r["reward"] for r in scored_rows) / len(scored_rows), 4)
                               if scored_rows else None),
        "status_counts": {st: sum(r["status"] == st for r in results)
                          for st in sorted({r["status"] for r in results})},
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tasks", help="comma-separated scenario ids or task ids (default: all)")
    ap.add_argument("--task-file", type=Path, default=GYM / "tasks" / "migration_review.jsonl")
    ap.add_argument("--response-format", choices=("schema", "json_object", "none"),
                    default="schema",
                    help="schema: the controller planner's plan schema (default)")
    ap.add_argument("--retries", type=int, default=2, help="transport retries per task")
    ap.add_argument("--dry-run", action="store_true",
                    help="answer from a local fake endpoint instead of MIGPROTO_LLM_BASE_URL")
    ap.add_argument("--out", type=Path, default=RESULTS)
    args = ap.parse_args(argv)
    env.apply()   # before migproto.config is imported: Gym ports, Redis DB and lock
    if args.dry_run:
        import os

        with FakeEndpoint() as fake:
            os.environ.update(MIGPROTO_LLM_BASE_URL=fake.base_url,
                              MIGPROTO_LLM_MODEL=FAKE_MODEL, MIGPROTO_LLM_API_KEY="unused")
            out = evaluate(args.task_file, args.tasks, settings_from_env(),
                           args.response_format, args.retries, args.out, dry_run=True)
    else:
        out = evaluate(args.task_file, args.tasks, settings_from_env(), args.response_format,
                       args.retries, args.out)
    print(f"results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
