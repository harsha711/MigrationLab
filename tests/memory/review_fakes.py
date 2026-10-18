"""Fakes for the Run Review tests: same interfaces as RunLog, migproto.lock, run_review."""

import itertools
import json
import threading


_counter = itertools.count()


class FakeRunLog:
    """Same surface the manager uses from migproto.runlog.RunLog."""

    def __init__(self, root, command):
        self.run_id = f"20261017-18{next(_counter):04d}-{command}"
        self.dir = root / self.run_id
        self.dir.mkdir(parents=True)
        self.t = 0.0

    def event(self, kind, provenance="runner", **data):
        self.t += 0.1
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": self.t, "kind": kind, "provenance": provenance, **data}) + "\n")

    def write(self, name, text):
        (self.dir / name).write_text(text)

    def summary(self, verdict, **result):
        (self.dir / "summary.json").write_text(json.dumps({"verdict": verdict, **result}))


class FakeLock:
    class ExecutionBusy(Exception):
        def __init__(self, owner):
            super().__init__("Another MigrationLab execution is active.")
            self.owner = owner

    def __init__(self):
        self.holder = None
        self.owner = {}

    def acquire(self, command):
        if self.holder is not None:
            raise self.ExecutionBusy(dict(self.owner))
        self.holder = threading.get_ident()
        self.owner = {"command": command}

    def update_owner(self, **kw):
        self.owner.update(kw)

    def release(self):
        self.holder = None


def make_controller(gate: threading.Event | None = None, fail=False):
    calls = []

    def run_review(log, source, request=None, topology=None, llm=None, scenario=None):
        calls.append((source, topology))
        log.event("agent_config", provenance="scripted", source=source, topology=topology)
        log.event("attempt", provenance="scripted", name="attempt 1", plan_id="a1-plan")
        log.event("check", provenance="manual", name="case.S0.old_smoke", status="PASS",
                  steps=[{"app": "old", "op": "write", "ok": True},
                         {"app": "old", "op": "read", "ok": True}])
        if gate:
            gate.wait(5)
        if fail:
            raise RuntimeError("controller blew up")
        return {"ok": True, "verdict": "PASS", "source": source, "topology": topology,
                "provenance": "scripted", "attempts": []}

    run_review.calls = calls
    return run_review
