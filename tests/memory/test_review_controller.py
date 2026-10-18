"""Run Review reaches the real controller (agents.loop.run_review) under a real lock file.

Only the controller's inner _run_review is replaced, so no Saleor server or database starts.
Everything between the UI and it is real: ReviewManager's lazy imports of
migproto.agents.controller and migproto.lock, the flock on MIGPROTO_LOCK_PATH, RunLog,
loop.run_review's tracing wrapper, summary and store ingestion.
"""

import fcntl
import json
import threading
import time

import pytest

from migproto import lock, runlog
from migproto.agents import controller, loop
from migproto.memory import store
from migproto.ui.review import ReviewManager


@pytest.fixture
def real_lock(tmp_path, monkeypatch):
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "migproto.lock"
    monkeypatch.setenv("MIGPROTO_LOCK_PATH", str(path))
    yield path
    assert not lock.held()


def wait_done(manager, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if manager.current and manager.current["state"] == "done":
            return manager.current
        time.sleep(0.02)
    raise AssertionError(f"review did not finish: {manager.current}")


def test_ui_review_reaches_loop_run_review_under_a_real_lock_file(tmp_path, real_lock,
                                                                 monkeypatch):
    assert controller.run_review is loop.run_review
    runs = tmp_path / "runs"
    seen, release = {}, threading.Event()

    def inner(log, source, request=None, topology=None, llm=None, scenario=None, trace=None):
        seen.update(source=source, topology=topology, run_id=log.run_id,
                    held=lock.held(), owner=json.loads(real_lock.read_text()))
        # Another open file description cannot take the flock while the review holds it.
        with real_lock.open("a+") as fh:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release.wait(5)
        log.check("fixture_check", True, msg="stub controller body")
        return {"ok": True, "verdict": "PASS", "topology": topology}

    monkeypatch.setattr(loop, "_run_review", inner)
    conn = store.connect(":memory:")
    manager = ReviewManager(conn, threading.Lock(), runs_root=runs,
                            runlog_factory=lambda command: runlog.RunLog(command, root=runs))
    assert manager.available() == {"ok": True, "reason": None}

    run_id = manager.start("scripted", "single")["run_id"]
    for _ in range(200):
        if seen:
            break
        time.sleep(0.02)
    assert seen["source"] == "scripted" and seen["topology"] == "single"
    assert seen["run_id"] == run_id and seen["held"] is True
    assert seen["owner"]["command"] == "ui-review-scripted"
    assert seen["owner"]["run_id"] == run_id
    queued = manager.start("scripted", "roles")
    assert [job["state"] for job in manager.jobs()] == ["running", "queued"]
    release.set()

    done = wait_done(manager)
    assert (done["verdict"], done["error"]) == ("PASS", None)
    for _ in range(200):
        if manager.jobs()[-1]["state"] == "finished":
            break
        time.sleep(.02)
    assert manager.jobs()[-1]["run_id"] == queued["run_id"]
    summary = json.loads((runs / run_id / "summary.json").read_text())
    assert summary["verdict"] == "PASS" and summary["topology"] == "single"
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0] == 1
    # Released: a fresh flock succeeds.
    with real_lock.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)


def test_ui_review_records_lock_error_when_an_external_execution_is_active(tmp_path, real_lock,
                                                                            monkeypatch):
    called = []
    monkeypatch.setattr(loop, "_run_review", lambda *a, **k: called.append(1) or {"ok": True})
    with real_lock.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manager = ReviewManager(store.connect(":memory:"), threading.Lock(),
                                runs_root=tmp_path / "runs")
        started = manager.start("scripted", "roles")
        for _ in range(100):
            if manager.jobs()[0]["state"] == "finished":
                break
            time.sleep(.02)
        assert manager.jobs()[0]["run_id"] == started["run_id"]
        assert manager.jobs()[0]["verdict"] == "ERROR"
        fcntl.flock(fh, fcntl.LOCK_UN)
    assert called == []


def test_ui_job_writes_v1_request_and_human_decision(tmp_path):
    from review_fakes import FakeLock, FakeRunLog

    runs = tmp_path / "runs"
    manager = ReviewManager(store.connect(":memory:"), threading.Lock(), runs_root=runs,
                            controller=lambda log, source, **kw: {"ok": True, "verdict": "PASS"},
                            lock_module=FakeLock(),
                            runlog_factory=lambda command: FakeRunLog(runs, command))
    with pytest.raises(ValueError, match="unknown scenario"):
        manager.start("scripted", "roles", "does-not-exist")
    started = manager.start("scripted", "parallel", "rename-note", {"max_revisions": 0})
    request = json.loads((runs / started["run_id"] / "request.json").read_text())
    assert request["contract_version"] == "migproto.contracts/v1"
    assert request["scenario"] == "rename-note" and request["agent_topology"] == "parallel"
    for _ in range(100):
        if manager.jobs()[0]["state"] == "finished":
            break
        time.sleep(.02)
    record = manager.decision(started["run_id"], "approve", "runner evidence reviewed")
    assert record["provenance"] == "human"
    assert json.loads((runs / started["run_id"] / "review-decision.json").read_text()) == record
