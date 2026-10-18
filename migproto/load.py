"""Write load and lock observation during a migration stage (large-table scenarios C1-C6).

`LoadGenerator` keeps a few threads issuing `customerUpdate(note)` against one app version, each
write setting a row's note to the value it already has: no data changes, but every write takes
the row lock a real update would, so migration locks that block writers show up as latency.
`LockMonitor` polls pg_stat_activity for sessions waiting on locks in the run database.

These are measurements on one machine for one plan, not a proof of concurrency safety.
"""

import itertools
import threading
import time

from . import db


def summarize(samples: list[tuple[float, float, bool, str | None]]) -> dict:
    """samples: (seconds since start, latency ms, ok, error)."""
    lat = sorted(s[1] for s in samples)
    errors = [s for s in samples if not s[2]]

    def pct(p):
        if not lat:
            return None
        return round(lat[min(len(lat) - 1, int(round(p / 100 * (len(lat) - 1))))], 1)

    worst = sorted(samples, key=lambda s: -s[1])[:3]
    return {
        "writes": len(samples),
        "errors": len(errors),
        "p50_ms": pct(50), "p95_ms": pct(95),
        "max_ms": round(lat[-1], 1) if lat else None,
        "worst": [{"t_s": round(t, 2), "ms": round(ms, 1), "ok": ok, "error": (err or "")[:160]}
                  for t, ms, ok, err in worst],
        "first_errors": [(e[3] or "")[:160] for e in errors[:3]],
    }


class LoadGenerator:
    def __init__(self, server, targets: dict[int, str | None], threads: int = 4):
        self.server = server
        self.targets = list(targets.items())
        self.threads = threads
        self.samples: list = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._t0 = 0.0

    def _work(self, offset: int) -> None:
        cycle = itertools.cycle(self.targets[offset::self.threads] or self.targets)
        while not self._stop.is_set():
            pk, value = next(cycle)
            t = time.perf_counter()
            try:
                res = self.server.write_note(pk, value)
                ok, error = res.ok, res.error
            except Exception as exc:
                # e.g. requests.ReadTimeout when a write stays blocked past the client timeout:
                # that is the strongest possible signal, so it must be recorded, not lost.
                ok, error = False, f"{type(exc).__name__}: {exc}"
            ms = (time.perf_counter() - t) * 1000
            with self._lock:
                self.samples.append((t - self._t0, ms, ok, error))

    def start(self) -> "LoadGenerator":
        self._t0 = time.perf_counter()
        self._workers = [threading.Thread(target=self._work, args=(i,), daemon=True,
                                          name=f"migproto-load-{i}") for i in range(self.threads)]
        for w in self._workers:
            w.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        for w in self._workers:
            w.join(timeout=120)
        with self._lock:
            return summarize(list(self.samples))


LOCK_WAIT_SQL = (
    "SELECT coalesce(max(extract(epoch FROM now() - query_start)) * 1000, 0), count(*) "
    "FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock'")


class LockMonitor:
    def __init__(self, dbname: str, interval_s: float = 0.1):
        self.dbname, self.interval_s = dbname, interval_s
        self.max_wait_ms = 0.0
        self.max_blocked = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _work(self) -> None:
        try:
            with db.connect("postgres") as conn:
                while not self._stop.is_set():
                    wait_ms, blocked = conn.execute(LOCK_WAIT_SQL, (self.dbname,)).fetchone()
                    self.max_wait_ms = max(self.max_wait_ms, float(wait_ms or 0))
                    self.max_blocked = max(self.max_blocked, int(blocked or 0))
                    self._stop.wait(self.interval_s)
        except Exception:   # observation only; never break the run
            pass

    def start(self) -> "LockMonitor":
        self._thread = threading.Thread(target=self._work, daemon=True, name="migproto-lockmon")
        self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        return {"max_lock_wait_ms": round(self.max_wait_ms, 1), "max_blocked_sessions": self.max_blocked}


class LongTransaction:
    """Holds an ACCESS SHARE lock on account_user for `seconds` (scenario C4)."""

    def __init__(self, dbname: str, seconds: float):
        self.dbname, self.seconds = dbname, seconds
        self._thread: threading.Thread | None = None
        self.started = threading.Event()

    def _work(self) -> None:
        with db.connect(self.dbname, autocommit=False) as conn:
            conn.execute("SELECT id FROM account_user ORDER BY id LIMIT 1").fetchone()
            self.started.set()
            time.sleep(self.seconds)
            conn.rollback()

    def start(self) -> "LongTransaction":
        self._thread = threading.Thread(target=self._work, daemon=True, name="migproto-longtx")
        self._thread.start()
        self.started.wait(10)
        return self

    def join(self) -> None:
        if self._thread:
            self._thread.join(timeout=self.seconds + 30)
