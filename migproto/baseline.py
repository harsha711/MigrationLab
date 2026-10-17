"""Baseline: seed template DB, smoke-test the old app, run an existing test subset."""

import re
import subprocess
import time

from . import db, seed
from .config import PORTS, SEED_CUSTOMERS, SEED_DB, VENV_PY, WORKTREES
from .runlog import MANUAL, RunLog
from .saleor_app import AppServer, manage, saleor_env

PYTEST_SUBSET = [
    "saleor/graphql/account/tests/mutations/staff/test_customer_create.py",
    "saleor/graphql/account/tests/mutations/staff/test_customer_update.py",
    "saleor/graphql/account/tests/queries/test_me.py",
    "saleor/graphql/account/tests/bulk_mutations/test_customer_bulk_update.py",
]
PYTEST_VERSIONS = ["old", "new-unsafe"]


def seed_ready() -> bool:
    if not db.exists(SEED_DB):
        return False
    try:
        return len(seed.seed_rows(SEED_DB, "note")) == SEED_CUSTOMERS
    except Exception:
        return False


def build_seed(log: RunLog) -> None:
    db.drop(SEED_DB)
    db.create(SEED_DB)
    steps = [
        ("migrate", ["migrate", "-v", "1"]),
        ("populatedb", ["populatedb", "--createsuperuser", "--withoutimages"]),
        ("seed_customers", ["shell", "-c", seed.django_seed_script(seed.synthetic_notes())]),
    ]
    for name, args in steps:
        t0 = time.monotonic()
        proc = manage("old", args, SEED_DB, log.dir / f"seed/{name}.log")
        log.event("seed_step", name=name, exit=proc.returncode,
                  seconds=round(time.monotonic() - t0, 1))
        if proc.returncode:
            raise RuntimeError(f"BLOCKER: seed step {name} failed, see {log.dir}/seed/{name}.log")
    counts = db.query(SEED_DB, "SELECT count(*), count(note) FROM account_user")[0]
    log.event("seed_ready", users=counts[0], users_with_note=counts[1])


def smoke_old(log: RunLog) -> bool:
    run_db = db.new_run_db("baseline")
    server = AppServer("old", "old", PORTS["old"], run_db, log.dir / "server-old.log")
    ok = True
    try:
        server.start()
        rows = seed.seed_rows(run_db, "note")
        pk = next(k for k, v in rows.items() if v)
        r = server.read_note(pk)
        ok &= log.check("old.read_note", r.ok and r.value == rows[pk], user=pk, result=r.as_dict())
        w = server.write_note(pk, "baseline write")
        r2 = server.read_note(pk)
        ok &= log.check("old.write_then_read", w.ok and r2.value == "baseline write",
                        write=w.as_dict(), read=r2.as_dict())
        c = server.create_customer("baseline-new@example.test", "created in baseline")
        r3 = server.read_note(c.value) if c.ok else c
        ok &= log.check("old.create_then_read", c.ok and r3.value == "created in baseline",
                        create=c.as_dict(), read=r3.as_dict())
        db_note = db.query(run_db, "SELECT note FROM account_user WHERE id=%s", (pk,))[0][0]
        ok &= log.check("db.note_column_written", db_note == "baseline write", value=db_note)
    finally:
        server.stop()
        db.drop(run_db)
    return ok


def run_pytest(log: RunLog, version: str) -> bool:
    dbname = f"migproto_pytest_{version.replace('-', '_')}"
    cmd = [str(VENV_PY), "-m", "pytest", "-n", "0", "--reuse-db", "-q", "-p", "no:cacheprovider",
           *PYTEST_SUBSET]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=WORKTREES[version], env=saleor_env(dbname),
                          capture_output=True, text=True, timeout=1800)
    out = proc.stdout + "\n" + proc.stderr
    log.write(f"pytest-{version}.log", f"$ (cd {WORKTREES[version]} && {' '.join(cmd)})\n{out}")
    m = re.findall(r"(\d+) (passed|failed|error|errors|skipped)", out)
    counts = {k: int(v) for v, k in m}
    return log.check(f"pytest.{version}", proc.returncode == 0, provenance=MANUAL,
                     tests=PYTEST_SUBSET, counts=counts, exit=proc.returncode,
                     seconds=round(time.monotonic() - t0, 1))


def run(log: RunLog, rebuild_seed: bool = False, skip_pytest: bool = False) -> dict:
    if rebuild_seed or not seed_ready():
        build_seed(log)
    else:
        log.event("seed_reused", name=SEED_DB)
    ok = smoke_old(log)
    if not skip_pytest:
        for version in PYTEST_VERSIONS:
            ok &= run_pytest(log, version)
    return {"ok": ok}
