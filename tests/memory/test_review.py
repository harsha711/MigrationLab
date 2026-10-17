"""Run Review manager: controller-only execution, one at a time, live event tail.

The real controller (Owner 2) and lock (Harsha) live on other branches and need Linux plus
Docker to run a review, so these tests inject fakes with the same interfaces.
"""

import json
import threading
import time

import pytest

from migproto.memory import query, store
from migproto.ui import review as review_mod
from migproto.ui.review import Busy, ReviewManager, Unavailable

from review_fakes import FakeLock, FakeRunLog, make_controller


@pytest.fixture
def env(tmp_path):
    conn = store.connect(":memory:")
    runs = tmp_path / "runs"
    runs.mkdir()
    return conn, runs


def manager(env, controller, lock=None):
    conn, runs = env
    return ReviewManager(conn, threading.Lock(), runs_root=runs, controller=controller,
                         lock_module=lock or FakeLock(),
                         runlog_factory=lambda command: FakeRunLog(runs, command))


def wait_done(m, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if m.current and m.current["state"] == "done":
            return
        time.sleep(0.02)
    raise AssertionError("review did not finish")


def test_unavailable_without_controller_or_lock(env, monkeypatch):
    conn, runs = env
    real_import = review_mod.importlib.import_module

    def missing(name):
        if name in ("migproto.agents.controller", "migproto.lock"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name)

    monkeypatch.setattr(review_mod.importlib, "import_module", missing)
    m = ReviewManager(conn, threading.Lock(), runs_root=runs)
    status = m.available()
    assert status["ok"] is False and status["reason"]
    with pytest.raises(Unavailable):
        m.start("scripted", "roles")


def test_available_with_merged_controller_and_lock(env):
    conn, runs = env
    status = ReviewManager(conn, threading.Lock(), runs_root=runs).available()
    assert status == {"ok": True, "reason": None}


def test_review_runs_through_controller_and_is_ingested(env):
    controller = make_controller()
    m = manager(env, controller)
    started = m.start("scripted", "single")
    wait_done(m)
    run_id = started["run_id"]
    assert controller.calls == [("scripted", "single")]
    assert run_id.endswith("agent-loop-scripted")
    assert m.current == {"run_id": run_id, "mode": "scripted", "topology": "single",
                         "state": "done", "verdict": "PASS", "error": None}
    conn, runs = env
    summary = json.loads((runs / run_id / "summary.json").read_text())
    assert summary["verdict"] == "PASS" and "verdict" not in {k for k in summary if k != "verdict"}
    assert query.run_detail(conn, run_id)["meta"]["verdict"] == "PASS"


def test_controller_exception_recorded_as_error(env):
    m = manager(env, make_controller(fail=True))
    run_id = m.start("scripted", "roles")["run_id"]
    wait_done(m)
    assert m.current["verdict"] == "ERROR"
    conn, runs = env
    assert (runs / run_id / "error.txt").is_file()
    assert json.loads((runs / run_id / "summary.json").read_text())["verdict"] == "ERROR"


def test_one_review_at_a_time(env):
    gate = threading.Event()
    lock = FakeLock()
    m = manager(env, make_controller(gate), lock)
    m.start("scripted", "roles")
    with pytest.raises(Busy):
        m.start("scripted", "roles")          # same UI: in-process guard
    other = manager(env, make_controller(), lock)
    with pytest.raises(Busy) as exc:
        other.start("llm", "roles")           # e.g. a CLI run holding the execution lock
    assert exc.value.owner["command"] == "ui-review-scripted"
    gate.set()
    wait_done(m)
    assert lock.holder is None                # released after the review


def test_index_failure_does_not_crash_the_review(env, monkeypatch):
    def broken_ingest(conn, run_dir):
        raise ValueError("bad events file")
    monkeypatch.setattr(review_mod.store, "ingest", broken_ingest)
    lock = FakeLock()
    m = manager(env, make_controller(), lock)
    m.start("scripted", "roles")
    wait_done(m)
    assert m.current["verdict"] == "PASS"
    assert m.current["error"].startswith("run recorded but not indexed")
    assert lock.holder is None


def test_invalid_input(env):
    m = manager(env, make_controller())
    with pytest.raises(ValueError):
        m.start("shell", "roles")
    with pytest.raises(ValueError):
        m.start("scripted", "swarm")


def test_event_tail_pages_and_rejects_bad_ids(env):
    gate = threading.Event()
    m = manager(env, make_controller(gate))
    run_id = m.start("scripted", "roles")["run_id"]
    time.sleep(0.2)
    first = m.events(run_id)
    assert [e["kind"] for e in first["events"]] == ["agent_config", "attempt", "check"]
    assert first["done"] is False
    conn, runs = env
    with (runs / run_id / "events.jsonl").open("a") as fh:
        fh.write('{"kind": "partial')              # line still being written
    again = m.events(run_id, first["next"])
    assert again["events"] == [] and again["next"] == first["next"]
    with (runs / run_id / "events.jsonl").open("a") as fh:
        fh.write('"}\n')                            # writer finishes the line
    assert [e["kind"] for e in m.events(run_id, first["next"])["events"]] == ["partial"]
    gate.set()
    wait_done(m)
    assert m.events(run_id, first["next"])["done"] is True
    for bad in ("../secrets", "..", "20261017-180000-x/../../y", "", None):
        with pytest.raises(ValueError):
            m.events(bad)
