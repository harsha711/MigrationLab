"""Repository-wide execution lock for anything that uses the fixed ports, Saleor servers,
or disposable migration databases. CLI, runner API, UI and Gym adapter all use this one.

The lock is an flock(2) on a file under runs/. The kernel releases it when the holder
exits or crashes, so a leftover file never blocks anyone; its JSON body is only owner
metadata for error messages. Local filesystems only.
"""

import fcntl
import json
import os
import socket
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import RUNS

BUSY_MESSAGE = "Another MigrationLab execution is active."


class ExecutionBusy(RuntimeError):
    def __init__(self, owner: dict | None, detail: str = ""):
        self.owner = owner
        who = ""
        if owner:
            who = (f" Owner: pid {owner.get('pid')} on {owner.get('host')}, "
                   f"command {owner.get('command')!r}, run {owner.get('run_id')}, "
                   f"since {owner.get('started_at')}.")
        super().__init__(BUSY_MESSAGE + who + (f" {detail}" if detail else ""))


def lock_path() -> Path:
    return Path(os.environ.get("MIGPROTO_LOCK_PATH") or RUNS / ".migproto-execution.lock")


_guard = threading.Lock()
_fh = None
_holder: int | None = None
_depth = 0
_owner: dict = {}


def read_owner(path: Path | None = None) -> dict | None:
    try:
        text = (path or lock_path()).read_text()
        return json.loads(text) if text.strip() else None
    except (OSError, ValueError):
        return None


def held() -> bool:
    return _depth > 0


def _write_owner() -> None:
    _fh.seek(0)
    _fh.truncate()
    _fh.write(json.dumps(_owner))
    _fh.flush()


def acquire(command: str, run_id: str | None = None) -> None:
    """Take the lock or raise ExecutionBusy immediately. Re-entrant within one thread."""
    global _fh, _holder, _depth, _owner
    me = threading.get_ident()
    with _guard:
        if _depth:
            if _holder == me:
                _depth += 1
                return
            raise ExecutionBusy(dict(_owner), "(held by another thread in this process)")
        path = lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = path.open("a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise ExecutionBusy(read_owner(path)) from None
        _fh, _holder, _depth = fh, me, 1
        _owner = {"pid": os.getpid(), "host": socket.gethostname(), "command": command,
                  "run_id": run_id, "argv": sys.argv[1:],
                  "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write_owner()


def update_owner(**fields) -> None:
    with _guard:
        if _depth and _holder == threading.get_ident():
            _owner.update(fields)
            _write_owner()


def release() -> None:
    global _fh, _holder, _depth
    with _guard:
        if not _depth or _holder != threading.get_ident():
            return
        _depth -= 1
        if _depth:
            return
        try:
            _fh.seek(0)
            _fh.truncate()
            fcntl.flock(_fh, fcntl.LOCK_UN)
        finally:
            _fh.close()
            _fh, _holder = None, None
            _owner.clear()


@contextmanager
def execution_lock(command: str, run_id: str | None = None):
    acquire(command, run_id)
    try:
        yield
    finally:
        release()
