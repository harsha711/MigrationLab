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
from pathlib import Path

from ..memory import store

MODES = ("scripted", "llm")
TOPOLOGIES = ("roles", "single")
RUN_ID = re.compile(r"^\d{8}-\d{6}-[\w.-]+$")
START_TIMEOUT_S = 15


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

    # ---- dependency discovery (lazy, cross-owner) ----

    def _load(self):
        controller = self._controller
        if controller is None:
            try:
                controller = importlib.import_module("migproto.agents.controller").run_review
            except (ImportError, AttributeError) as exc:
                raise Unavailable(f"controller not available ({exc})") from exc
        lock_module = self._lock_module
        if lock_module is None:
            try:
                lock_module = importlib.import_module("migproto.lock")
            except ImportError as exc:
                if getattr(exc, "name", "") == "fcntl":
                    raise Unavailable("execution lock unavailable on this platform "
                                      "(needs fcntl: Linux or macOS)") from exc
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

    # ---- starting a review ----

    def start(self, mode: str, topology: str) -> dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if topology not in TOPOLOGIES:
            raise ValueError(f"topology must be one of {TOPOLOGIES}")
        controller, lock_module, factory = self._load()
        with self._guard:
            if self.current and self.current["state"] == "running":
                raise Busy("a review started from this UI is still running",
                           {"run_id": self.current["run_id"]})
            ready: queue.Queue = queue.Queue()
            threading.Thread(target=self._work, daemon=True, name="migproto-ui-review",
                             args=(mode, topology, controller, lock_module, factory, ready)).start()
            try:
                kind, value, owner = ready.get(timeout=START_TIMEOUT_S)
            except queue.Empty:
                raise RuntimeError("review did not start in time") from None
        if kind == "busy":
            raise Busy(value, owner)
        if kind == "error":
            raise RuntimeError(value)
        return {"run_id": value}

    def _work(self, mode, topology, controller, lock_module, factory, ready):
        # The lock is per thread (re-entrant in the holder thread), so the worker takes it;
        # the controller's own execute_plan call re-enters it in this same thread.
        busy_error = getattr(lock_module, "ExecutionBusy", ())
        try:
            lock_module.acquire(f"ui-review-{mode}")
        except busy_error as exc:
            ready.put(("busy", str(exc), getattr(exc, "owner", None)))
            return
        except Exception as exc:
            ready.put(("error", f"could not take the execution lock: {exc}", None))
            return
        try:
            try:
                log = factory(f"agent-loop-{mode}")
            except Exception as exc:
                ready.put(("error", f"could not create the run log: {exc}", None))
                return
            if hasattr(lock_module, "update_owner"):
                lock_module.update_owner(run_id=log.run_id)
            self.current = {"run_id": log.run_id, "mode": mode, "topology": topology,
                            "state": "running", "verdict": None, "error": None}
            ready.put(("ok", log.run_id, None))
            error = None
            try:
                result = controller(log, mode, topology=topology)
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
            self.current.update(state="done", verdict=verdict, error=error)
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
