import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import RUNS

MANUAL = "manual"
SCRIPTED = "scripted"
RUNNER = "runner"

CONTRACT_VERSION = "migproto.contracts/v1"
CHECK_TYPES = ("assertion", "safety_gate", "execution")
OUTCOMES = ("pass", "fail", "refused", "error", "skipped")
APP_VERSIONS = ("old", "new", "both", "database", None)
FAILURE_OUTCOME = {"assertion": "fail", "safety_gate": "refused", "execution": "error"}
V0_STATUS = {"pass": "PASS", "fail": "FAIL", "refused": "FAIL", "error": "FAIL", "skipped": "SKIP"}
SCOPE_KEYS = ("contract_version", "run_id", "attempt_id", "step_id", "timestamp",
              "app_commit", "database_snapshot_id", "schema_state", "executed_by", "t")


def agent(model: str) -> str:
    return f"agent:{model}"


class RunLog:
    """Append-only execution log for one CLI invocation.

    v0 keys (`t`, `kind`, `provenance`, `name`, `status`, ...) are kept on every record;
    v1 scope keys are added alongside them (docs/contracts.md, section 4).
    """

    def __init__(self, command: str, root: Path = RUNS):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root.mkdir(parents=True, exist_ok=True)
        # Created exclusively: two commands started in the same second (parallel supervisors
        # sharing one runs/ directory) once logged into one directory. The second gets "-2".
        for n in range(1, 100):
            self.dir = root / (f"{stamp}-{command}" + (f"-{n}" if n > 1 else ""))
            try:
                self.dir.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError(f"no free run directory for {stamp}-{command}")
        self.command = command
        self.run_id = self.dir.name
        self.started = time.monotonic()
        self._events = self.dir / "events.jsonl"
        self._seq = 0
        self._attempt_events = 0
        self.attempt = 1
        self.checks: list[dict] = []
        self.listeners: list = []
        self.start_states: dict[str, str] = {}
        self.scope: dict = {}
        self._reset_scope()

    @property
    def attempt_id(self) -> str:
        return f"{self.run_id}/a{self.attempt}"

    def _reset_scope(self) -> None:
        self.scope = {"step_id": None, "schema_state": None, "database_snapshot_id": None,
                      "app_commits": {}}

    def set_attempt(self, attempt: int) -> None:
        if attempt != self.attempt:
            self.attempt = attempt
            self._reset_scope()

    def set_step(self, step_id: str | None) -> None:
        self.scope["step_id"] = step_id

    def event(self, kind: str, provenance: str = RUNNER, **data) -> dict:
        if kind == "attempt":
            self._attempt_events += 1
            self.set_attempt(self._attempt_events)
        record = {
            "t": round(time.monotonic() - self.started, 3),
            "kind": kind,
            "provenance": provenance,
            **data,
        }
        record.setdefault("contract_version", CONTRACT_VERSION)
        record.setdefault("run_id", self.run_id)
        record.setdefault("attempt_id", self.attempt_id)
        record.setdefault("step_id", self.scope["step_id"])
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        with self._events.open("a") as fh:
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self._seq += 1
        for listener in list(self.listeners):
            try:
                listener(record)
            except Exception as exc:  # a progress consumer must never break evidence capture
                print(f"progress listener failed: {exc}", flush=True)
        detail = data.get("name") or data.get("stage") or data.get("msg") or ""
        status = data.get("status", "")
        print(f"[{record['t']:8.1f}s] {kind:<14} {status:<6} {detail}", flush=True)
        return record

    def _app_commit(self, app_version: str | None) -> dict | None:
        roles = {"old": ["old"], "new": ["new"], "both": ["old", "new"]}.get(app_version)
        if not roles:
            return None
        commits = self.scope["app_commits"]
        return {r: commits.get(r) for r in roles}

    def check(self, name: str, passed: bool, provenance: str = RUNNER, *,
              app_version: str | None = None, check_type: str = "assertion",
              expected=None, observed=None, outcome: str | None = None, **data) -> bool:
        """Record one runner observation. Only deterministic runner code calls this."""
        if check_type not in CHECK_TYPES:
            raise ValueError(f"check_type {check_type!r}")
        if app_version not in APP_VERSIONS:
            raise ValueError(f"app_version {app_version!r}")
        if outcome is None:
            outcome = "pass" if passed else FAILURE_OUTCOME[check_type]
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome {outcome!r}")
        if outcome != "skipped" and (outcome == "pass") != bool(passed):
            raise ValueError(f"outcome {outcome!r} inconsistent with passed={passed}")
        record = self.event(
            "check", provenance, name=name, status=V0_STATUS[outcome], **data,
            observation_id=f"{self.attempt_id}/{name}#{self._seq}",
            outcome=outcome, check_type=check_type, app_version=app_version,
            app_commit=self._app_commit(app_version),
            schema_state=self.scope["schema_state"],
            database_snapshot_id=self.scope["database_snapshot_id"],
            expected=expected, observed=observed, executed_by=RUNNER,
        )
        self.checks.append(record)
        return passed

    def skip(self, name: str, reason: str, *, app_version: str | None = None,
             check_type: str = "execution", **data) -> None:
        self.check(name, False, app_version=app_version, check_type=check_type,
                   outcome="skipped", msg=reason, **data)

    def write(self, rel: str, text: str) -> Path:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_json(self, rel: str, obj) -> Path:
        return self.write(rel, json.dumps(obj, indent=2, default=str, ensure_ascii=False))

    def outcome_counts(self) -> dict[str, dict[str, int]]:
        per: dict[str, Counter] = {}
        for c in self.checks:
            per.setdefault(c["attempt_id"], Counter())[c["outcome"]] += 1
        return {a: {o: n.get(o, 0) for o in OUTCOMES} for a, n in per.items()}

    def summary(self, verdict: str, **data) -> dict:
        failed = [c["name"] for c in self.checks if c["status"] == "FAIL"]
        summary = {
            "command": self.command,
            "verdict": verdict,
            "elapsed_s": round(time.monotonic() - self.started, 1),
            "checks_total": len(self.checks),
            "checks_failed": failed,
            **data,
            "contract_version": CONTRACT_VERSION,
            "run_id": self.run_id,
            "outcome_counts": self.outcome_counts(),
        }
        self.write_json("summary.json", summary)
        self.event("summary", verdict=verdict, msg=f"{verdict} -> {self.dir}")
        return summary
