import json
import time
from datetime import datetime
from pathlib import Path

from .config import RUNS

MANUAL = "manual"
SCRIPTED = "scripted"
RUNNER = "runner"


def agent(model: str) -> str:
    return f"agent:{model}"


class RunLog:
    """Append-only execution log for one CLI invocation."""

    def __init__(self, command: str, root: Path = RUNS):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = root / f"{stamp}-{command}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.command = command
        self.started = time.monotonic()
        self._events = self.dir / "events.jsonl"
        self.checks: list[dict] = []

    def event(self, kind: str, provenance: str = RUNNER, **data) -> dict:
        record = {
            "t": round(time.monotonic() - self.started, 3),
            "kind": kind,
            "provenance": provenance,
            **data,
        }
        with self._events.open("a") as fh:
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        detail = data.get("name") or data.get("stage") or data.get("msg") or ""
        status = data.get("status", "")
        print(f"[{record['t']:8.1f}s] {kind:<14} {status:<6} {detail}", flush=True)
        return record

    def check(self, name: str, passed: bool, provenance: str = RUNNER, **data) -> bool:
        status = "PASS" if passed else "FAIL"
        record = self.event("check", provenance, name=name, status=status, **data)
        self.checks.append(record)
        return passed

    def write(self, rel: str, text: str) -> Path:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_json(self, rel: str, obj) -> Path:
        return self.write(rel, json.dumps(obj, indent=2, default=str, ensure_ascii=False))

    def summary(self, verdict: str, **data) -> dict:
        failed = [c["name"] for c in self.checks if c["status"] == "FAIL"]
        summary = {
            "command": self.command,
            "verdict": verdict,
            "elapsed_s": round(time.monotonic() - self.started, 1),
            "checks_total": len(self.checks),
            "checks_failed": failed,
            **data,
        }
        self.write_json("summary.json", summary)
        self.event("summary", verdict=verdict, msg=f"{verdict} -> {self.dir}")
        return summary
