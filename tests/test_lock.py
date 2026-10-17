import json
import os
import subprocess
import sys
import threading
import time

import pytest

from migproto import cli, lock

HOLDER = """
import sys, time
from migproto import lock
lock.acquire("holder", run_id="r-holder")
print("held", flush=True)
time.sleep(float(sys.argv[1]))
"""


@pytest.fixture(autouse=True)
def lock_file(tmp_path, monkeypatch):
    path = tmp_path / "exec.lock"
    monkeypatch.setenv("MIGPROTO_LOCK_PATH", str(path))
    yield path
    while lock.held():
        lock.release()


@pytest.fixture
def holder(lock_file):
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, "30"], stdout=subprocess.PIPE,
                            text=True, env=dict(os.environ))
    assert proc.stdout.readline().strip() == "held"
    yield proc
    proc.kill()
    proc.wait()


def test_other_process_fails_fast_with_owner(holder):
    t0 = time.monotonic()
    with pytest.raises(lock.ExecutionBusy) as err:
        lock.acquire("staged")
    assert time.monotonic() - t0 < 1
    assert str(err.value).startswith("Another MigrationLab execution is active.")
    assert err.value.owner["pid"] == holder.pid
    assert err.value.owner["command"] == "holder" and err.value.owner["run_id"] == "r-holder"


def test_lock_released_when_holder_dies(holder, lock_file):
    holder.kill()
    holder.wait()
    assert json.loads(lock_file.read_text())["pid"] == holder.pid  # stale metadata left behind
    with lock.execution_lock("staged"):
        assert lock.read_owner()["pid"] == os.getpid()
    assert lock.read_owner() is None


def test_reentrant_in_same_thread_and_released_on_exception():
    with pytest.raises(RuntimeError):
        with lock.execution_lock("all"):
            with lock.execution_lock("staged"):
                assert lock.held()
                raise RuntimeError("boom")
    assert not lock.held()
    with lock.execution_lock("again"):
        pass


def test_other_thread_in_same_process_is_refused():
    errors = []
    with lock.execution_lock("ui"):
        t = threading.Thread(target=lambda: _try(errors))
        t.start()
        t.join()
    assert errors and isinstance(errors[0], lock.ExecutionBusy)


def _try(errors):
    try:
        lock.acquire("gym")
    except lock.ExecutionBusy as exc:
        errors.append(exc)


def test_update_owner_records_run_id():
    with lock.execution_lock("staged"):
        lock.update_owner(run_id="20261017-000000-staged")
        assert lock.read_owner()["run_id"] == "20261017-000000-staged"


def test_cli_refuses_without_creating_run_dir(holder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RunLog", lambda *a, **k: pytest.fail("RunLog created"))
    assert cli._run("staged", lambda log: {"ok": True}) == 2
    assert "Another MigrationLab execution is active." in capsys.readouterr().err
