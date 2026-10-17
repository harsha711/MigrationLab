"""Minimal OpenAI-compatible chat client (plain HTTP, no SDK)."""

import json
import os
import re
import time

import requests


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: int = 600):
        self.base_url = (base_url or os.environ.get("MIGPROTO_LLM_BASE_URL")
                         or "http://localhost:11434/v1").rstrip("/")
        self.model = model or os.environ.get("MIGPROTO_LLM_MODEL") or "gpt-oss:120b-cloud"
        self.api_key = api_key or os.environ.get("MIGPROTO_LLM_API_KEY") or "unused"
        self.timeout = timeout

    def chat_json(self, system: str, user: str, transcript: list | None = None,
                  retries: int = 2) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err = None
        for attempt in range(retries + 1):
            t0 = time.monotonic()
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages, "temperature": 0,
                      "response_format": {"type": "json_object"}},
                timeout=self.timeout,
            )
            elapsed = round(time.monotonic() - t0, 1)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if transcript is not None:
                    transcript.append({"attempt": attempt, "error": last_err, "seconds": elapsed})
                continue
            content = resp.json()["choices"][0]["message"].get("content") or ""
            if transcript is not None:
                transcript.append({"attempt": attempt, "messages": messages, "response": content,
                                   "seconds": elapsed, "usage": resp.json().get("usage")})
            try:
                return parse_json(content)
            except ValueError as exc:
                last_err = str(exc)
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That was not valid JSON ({exc}). "
                                                "Reply with one JSON object only."},
                ]
        raise LLMError(last_err or "no response")


def parse_json(text: str) -> dict:
    text = text.strip()
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
