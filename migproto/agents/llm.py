"""Budgeted OpenAI-compatible client for the shared vLLM deployment."""

import json
import os
import re
import time
from collections.abc import Callable

import requests

from . import tracing
from .budget import BudgetExceeded, ReviewBudget


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: int | None = None,
                 budget: ReviewBudget | None = None):
        self.base_url = (base_url or os.environ.get("MIGPROTO_LLM_BASE_URL")
                         or "http://localhost:8000/v1").rstrip("/")
        self.model = model or os.environ.get("MIGPROTO_LLM_MODEL")
        if not self.model:
            raise LLMError("MIGPROTO_LLM_MODEL is required for live model reviews")
        self.api_key = api_key or os.environ.get("MIGPROTO_LLM_API_KEY") or "unused"
        self.timeout = timeout or int(os.environ.get("MIGPROTO_LLM_TIMEOUT", "600"))
        self.budget = budget or ReviewBudget()
        self.temperature = float(os.environ.get("MIGPROTO_LLM_TEMPERATURE", "1.0"))
        self.top_p = float(os.environ.get("MIGPROTO_LLM_TOP_P", "0.95"))
        self.reasoning_enabled = _env_bool("MIGPROTO_LLM_REASONING", True)
        self.force_nonempty_content = _env_bool("MIGPROTO_LLM_FORCE_NONEMPTY", True)
        # vLLM's per-request cap on reasoning tokens; unset sends nothing (uncapped).
        budget_env = os.environ.get("MIGPROTO_LLM_THINKING_BUDGET")
        self.thinking_budget = int(budget_env) if budget_env else None
        self.trace_name = "llm"  # set by the controller to the current role

    def serving_metadata(self) -> dict:
        env = os.environ
        return {
            "revision": env.get("MIGPROTO_LLM_REVISION"),
            "request_timeout_s": self.timeout,
            "thinking_token_budget": self.thinking_budget,
            "serving_backend": env.get("MIGPROTO_SERVING_BACKEND", "vllm"),
            "serving_version": env.get("MIGPROTO_SERVING_VERSION"),
            "serving_flags": env.get("MIGPROTO_SERVING_FLAGS"),
            "hardware": env.get("MIGPROTO_SERVING_HARDWARE"),
            "driver": env.get("MIGPROTO_SERVING_DRIVER"),
            "cuda": env.get("MIGPROTO_SERVING_CUDA"),
            "brev_instance": env.get("MIGPROTO_BREV_INSTANCE"),
            "prefix_caching": _env_optional_bool("MIGPROTO_PREFIX_CACHING"),
            "reasoning_parser": env.get("MIGPROTO_REASONING_PARSER", "nemotron_v3"),
        }

    def chat_json(self, system: str, user: str, transcript: list | None = None,
                  retries: int = 2, validator: Callable[[dict], dict] | None = None,
                  response_schema: dict | None = None,
                  history: list[dict] | None = None) -> dict:
        if history is None:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]
        else:
            if not history:
                history.append({"role": "system", "content": system})
            history.append({"role": "user", "content": user})
            messages = history
        last_err = None
        for attempt in range(retries + 1):
            self.budget.begin_call()
            t0 = time.monotonic()
            usage = None
            content = ""
            reasoning = None
            call_err = None  # why this request failed; None once the reply validated
            budget_err = None
            trace_cm = tracing.generation(
                self.trace_name, self.model,
                {"temperature": self.temperature, "top_p": self.top_p,
                 "thinking_token_budget": self.thinking_budget, "attempt": attempt},
                messages)
            trace = trace_cm.__enter__()
            try:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                    "chat_template_kwargs": {
                        "force_nonempty_content": self.force_nonempty_content,
                        "enable_thinking": self.reasoning_enabled,
                    },
                    "response_format": (
                        {"type": "json_schema", "json_schema": response_schema}
                        if response_schema else {"type": "json_object"}
                    ),
                }
                if self.reasoning_enabled and self.thinking_budget is not None:
                    payload["thinking_token_budget"] = self.thinking_budget
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=max(1.0, min(self.timeout, self.budget.remaining_wall())),
                )
                elapsed = round(time.monotonic() - t0, 3)
                if resp.status_code != 200:
                    last_err = call_err = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    _record(transcript, attempt, messages, None, None, elapsed, last_err, False)
                    self.budget.finish_call(None, elapsed)
                    continue
                call_err = "invalid reply"
                body = resp.json()
                message = body["choices"][0]["message"]
                content = message.get("content") or ""
                # vLLM's reasoning parser returns reasoning_content; Ollama returns reasoning.
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                usage = body.get("usage")
                obj = parse_json(content)
                if validator:
                    obj = validator(obj)
                call_err = None
                _record(transcript, attempt, messages, content, usage, elapsed, None, True,
                        reasoning)
                self.budget.finish_call(usage, elapsed, valid=True)
                if history is not None:
                    history.append({"role": "assistant", "content": content})
                return obj
            except BudgetExceeded as exc:
                budget_err = str(exc)
                raise
            except requests.RequestException as exc:
                elapsed = round(time.monotonic() - t0, 3)
                last_err = call_err = f"request failed: {exc}"
                _record(transcript, attempt, messages, None, None, elapsed, last_err, False)
                self.budget.finish_call(None, elapsed)
                # No reply to correct: retry the same request without an extra turn.
                continue
            except (KeyError, TypeError, ValueError) as exc:
                elapsed = round(time.monotonic() - t0, 3)
                last_err = call_err = str(exc)
                _record(transcript, attempt, messages, content, usage, elapsed, last_err, False,
                        reasoning)
                self.budget.finish_call(usage, elapsed)
            finally:
                usage_details = ({"input": usage.get("prompt_tokens") or 0,
                                  "output": usage.get("completion_tokens") or 0}
                                 if isinstance(usage, dict) else None)
                level = "ERROR" if call_err else "WARNING" if budget_err else "DEFAULT"
                tracing.update(trace, output=content or None, usage_details=usage_details,
                               metadata={"reasoning": reasoning,
                                         "seconds": round(time.monotonic() - t0, 3)},
                               level=level, status_message=call_err or budget_err)
                trace_cm.__exit__(None, None, None)
            if attempt < retries:
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"The response was invalid ({last_err}). "
                                                "Reply with one valid JSON object matching "
                                                "the requested schema."},
                ]
        raise LLMError(last_err or "no response")


def _record(transcript: list | None, attempt: int, messages: list, response: str | None,
            usage: dict | None, seconds: float, error: str | None, schema_valid: bool,
            reasoning: str | None = None) -> None:
    if transcript is not None:
        transcript.append({
            "attempt": attempt,
            "messages": list(messages),
            "response": response,
            "reasoning_content": reasoning,
            "seconds": seconds,
            "usage": usage,
            "error": error,
            "schema_valid": schema_valid,
        })


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def _env_optional_bool(name: str) -> bool | None:
    return None if name not in os.environ else _env_bool(name, False)


def parse_json(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("empty message.content")
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found")
        obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON must be an object")
    return obj
