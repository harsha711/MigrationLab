"""Run Review: start one live review through Owner 2's controller, never the runner directly.

The UI does not execute SQL or start servers. It calls
`migproto.agents.controller.run_review` (Owner 2), which calls the runner (Harsha), under
Harsha's execution lock (`migproto.lock`), and records the run exactly like `cli._run` does.
Both modules live on other branches until they merge, so they are imported lazily and the
feature reports itself unavailable (with the reason) when either is missing.

Live progress is read by tailing `runs/<run_id>/events.jsonl`, which the run log appends to
as each event is written.
"""

import importlib
import json
import queue
import re
import threading
import traceback
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path

from ..memory import store

MODES = ("scripted", "llm")
TOPOLOGIES = ("roles", "single", "parallel")
RUN_ID = re.compile(r"^\d{8}-\d{6}-[\w.-]+$")


class Unavailable(RuntimeError):
    """The controller or the execution lock cannot be used here."""


class Busy(RuntimeError):
    """Another review or MigrationLab execution is active."""

    def __init__(self, msg: str, owner: dict | None = None):
        super().__init__(msg)
        self.owner = owner


class ReviewManager:
    """One review at a time; dependencies injectable for tests."""

    def __init__(self, conn, db_lock, runs_root=None, controller=None, lock_module=None,
                 runlog_factory=None):
        self.conn, self.db_lock = conn, db_lock
        self.runs_root = Path(runs_root or store.ROOT / "runs")
        self._controller = controller
        self._lock_module = lock_module
        self._runlog_factory = runlog_factory
        self._guard = threading.Lock()
        self.current: dict | None = None
        self._jobs: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

    # ---- dependency discovery (lazy, cross-owner) ----

    def _load(self):
        controller = self._controller
        if controller is None:
            try:
                controller = importlib.import_module("migproto.agents.controller").run_review
            except (ImportError, AttributeError) as exc:
                if isinstance(exc, ImportError) and getattr(exc, "name", "") == "fcntl":
                    raise Unavailable("live review requires Linux or macOS because the "
                                      "execution lock uses fcntl") from exc
                raise Unavailable(f"controller not available ({exc})") from exc
        lock_module = self._lock_module
        if lock_module is None:
            try:
                lock_module = importlib.import_module("migproto.lock")
            except ImportError as exc:
                if getattr(exc, "name", "") == "fcntl":
                    raise Unavailable("live review requires Linux or macOS because the "
                                      "execution lock uses fcntl") from exc
                raise Unavailable(f"execution lock not available ({exc})") from exc
        factory = self._runlog_factory
        if factory is None:
            factory = importlib.import_module("migproto.runlog").RunLog
        return controller, lock_module, factory

    def available(self) -> dict:
        try:
            self._load()
            return {"ok": True, "reason": None}
        except Unavailable as exc:
            return {"ok": False, "reason": str(exc)}

    def status(self) -> dict:
        return {**self.available(), "current": self.current}

    def jobs(self) -> list[dict]:
        """Known UI jobs, oldest first. Run details remain in the evidence store."""
        with self._guard:
            return [deepcopy(job) for job in self._jobs.values()]

    # ---- starting a review ----

    def start(self, mode: str, topology: str, scenario: str = "rename-note",
              budget: dict | None = None, request_extra: dict | None = None) -> dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if topology not in TOPOLOGIES:
            raise ValueError(f"topology must be one of {TOPOLOGIES}")
        controller, lock_module, factory = self._load()
        try:
            request_module = importlib.import_module("migproto.agents.request")
            request = request_module.scenario_request(scenario)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        request.update(mode=mode, agent_topology=topology)
        if budget is not None:
            if not isinstance(budget, dict):
                raise ValueError("budget must be an object")
            request["budget"] = {**request.get("budget", {}), **budget}
        if request_extra:
            request.update(request_extra)
        with self._guard:
            try:
                log = factory(f"agent-loop-{mode}", root=self.runs_root)
            except TypeError:  # injectable factories in older tests
                log = factory(f"agent-loop-{mode}")
            request["request_id"] = f"ui-{log.run_id}"
            (log.dir / "request.json").write_text(json.dumps(request, indent=2) + "\n",
                                                  encoding="utf-8")
            job = {"run_id": log.run_id, "mode": mode, "topology": topology,
                   "scenario": scenario, "request": request, "state": "queued",
                   "verdict": None, "error": None, "submitted_at": _now()}
            self._jobs[log.run_id] = job
            self._queue.put((job, log, controller, lock_module))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._drain, daemon=True,
                                                name="migproto-ui-review-queue")
                self._worker.start()
            return {"run_id": log.run_id, "state": "queued"}

    def _drain(self):
        while True:
            try:
                job, log, controller, lock_module = self._queue.get_nowait()
            except queue.Empty:
                return
            self._work(job, log, controller, lock_module)

    def _work(self, job, log, controller, lock_module):
        # The lock is per thread (re-entrant in the holder thread), so the worker takes it;
        # the controller's own execute_plan call re-enters it in this same thread.
        busy_error = getattr(lock_module, "ExecutionBusy", ())
        try:
            lock_module.acquire(f"ui-review-{job['mode']}")
        except busy_error as exc:
            job.update(state="finished", verdict="ERROR", error=str(exc), finished_at=_now())
            return
        except Exception as exc:
            job.update(state="finished", verdict="ERROR", error=str(exc), finished_at=_now())
            return
        try:
            if hasattr(lock_module, "update_owner"):
                lock_module.update_owner(run_id=log.run_id)
            job.update(state="running", started_at=_now())
            self.current = job
            error = None
            try:
                result = controller(log, job["mode"], request=job["request"],
                                    topology=job["topology"], scenario=job["scenario"])
                # Same verdict rule as cli._run.
                verdict = result.pop("verdict", None) or ("PASS" if result.get("ok") else "FAIL")
                log.summary(verdict, **result)
            except Exception as exc:
                verdict, error = "ERROR", str(exc)
                log.write("error.txt", traceback.format_exc())
                log.summary("ERROR", error=error)
            try:
                with self.db_lock:
                    store.ingest(self.conn, log.dir)
            except Exception as exc:  # the run's files stay the evidence; only the index failed
                error = error or f"run recorded but not indexed: {exc}"
            # "done" only after indexing, so the UI never links to a run it cannot show.
            job.update(state="finished", verdict=verdict, error=error, finished_at=_now())
            # Legacy status endpoint calls a completed review "done"; the queue itself uses
            # the clearer finished state returned by /api/jobs.
            self.current = {**job, "state": "done"}
        finally:
            lock_module.release()

    # ---- live progress ----

    def events(self, run_id: str, after: int = 0) -> dict:
        """Complete event lines after index `after` of runs/<run_id>/events.jsonl."""
        if not run_id or not RUN_ID.match(run_id):
            raise ValueError("invalid run id")
        run_dir = (self.runs_root / run_id).resolve()
        if run_dir.parent != self.runs_root.resolve():
            raise ValueError("invalid run id")
        path = run_dir / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        out, nxt = [], max(0, after)
        for line in lines[nxt:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break  # a line still being written; pick it up on the next poll
            nxt += 1
        return {"run_id": run_id, "events": out, "next": nxt,
                "done": (run_dir / "summary.json").is_file()}

    def decision(self, run_id: str, decision: str, note: str = "", author: str = "UI user") -> dict:
        if not RUN_ID.match(run_id):
            raise ValueError("invalid run id")
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("note must be a string of at most 4000 characters")
        run_dir = (self.runs_root / run_id).resolve()
        if run_dir.parent != self.runs_root.resolve() or not (run_dir / "summary.json").is_file():
            raise ValueError("a finished recorded run is required")
        record = {"decision": decision, "note": note.strip(), "provenance": "human",
                  "author": author, "at": _now()}
        (run_dir / "review-decision.json").write_text(json.dumps(record, indent=2) + "\n",
                                                      encoding="utf-8")
        return record


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
