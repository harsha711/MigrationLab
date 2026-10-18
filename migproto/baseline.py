"""Baseline: seed template DB, smoke-test the old app, run an existing test subset."""

import re
import subprocess
import time

from . import db, olist, scope, seed
from .config import (OLIST_PREPARED_DIR, PORTS, SEED_CUSTOMERS, SEED_DB, SEED_EMAIL_DOMAIN,
                     VENV_PY, WORKTREES)
from .runlog import MANUAL, RunLog
from .saleor_app import AppServer, manage, saleor_env

PYTEST_SUBSET = [
    "saleor/graphql/account/tests/mutations/staff/test_customer_create.py",
    "saleor/graphql/account/tests/mutations/staff/test_customer_update.py",
    "saleor/graphql/account/tests/queries/test_me.py",
    "saleor/graphql/account/tests/bulk_mutations/test_customer_bulk_update.py",
]
PYTEST_VERSIONS = ["old", "new-unsafe"]


def seed_ready(source: str = "synthetic", expected: int = SEED_CUSTOMERS) -> bool:
    """The template holds exactly the requested seed: right source and customer count."""
    if not db.exists(SEED_DB):
        return False
    try:
        total, from_olist = db.query(
            SEED_DB,
            "SELECT count(*), count(*) FILTER (WHERE email LIKE %s) FROM account_user "
            "WHERE email LIKE %s",
            (f"{olist.EMAIL_PREFIX}%", f"%@{SEED_EMAIL_DOMAIN}"),
        )[0]
    except Exception:
        return False
    if source == "olist":
        return total == from_olist == expected
    return total == expected and from_olist == 0


def prepare_seed(source: str, olist_dir=None, limit: int | None = None) -> tuple[str, dict]:
    """Seed script plus provenance. Olist data is prepared into a git-ignored JSON file."""
    if source == "olist":
        if not olist_dir:
            raise RuntimeError("--seed-source olist needs --olist-dir (or MIGPROTO_OLIST_DIR)")
        path = OLIST_PREPARED_DIR / f"customers-{limit or 'all'}.json"
        provenance = olist.prepare(olist_dir, path, limit)
        return olist.django_seed_script(path), provenance
    if source != "synthetic":
        raise ValueError(f"unknown seed source {source!r}")
    return (seed.django_seed_script(seed.synthetic_notes()),
            {"source": "synthetic", "customers": SEED_CUSTOMERS})


def build_seed(log: RunLog, script: str | None = None, provenance: dict | None = None) -> None:
    log.set_step("seed")
    if script is None:
        script, provenance = prepare_seed("synthetic")
    log.event("seed_source", **(provenance or {}))
    db.drop(SEED_DB)
    db.create(SEED_DB)
    steps = [
        ("migrate", ["migrate", "-v", "1"]),
        ("populatedb", ["populatedb", "--createsuperuser", "--withoutimages"]),
        ("seed_customers", ["shell", "-c", script]),
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
    log.set_step("smoke_old")
    run_db = db.new_run_db("baseline")
    server = AppServer("old", "old", PORTS["old"], run_db, log.dir / "server-old.log")
    ok = True
    try:
        log.scope["app_commits"] = {"old": scope.worktree_head("old")}
        state = scope.schema_state(run_db)
        log.scope["schema_state"] = state
        log.scope["database_snapshot_id"] = scope.snapshot_id(run_db, state)
        server.start()
        rows = seed.seed_rows(run_db, "note")
        pk = next(k for k, v in rows.items() if v)
        r = server.read_note(pk)
        ok &= log.check("old.read_note", r.ok and r.value == rows[pk], user=pk, result=r.as_dict(),
                        app_version="old", expected={"value": rows[pk]},
                        observed={"ok": r.ok, "value": r.value, "error": r.error})
        w = server.write_note(pk, "baseline write")
        r2 = server.read_note(pk)
        ok &= log.check("old.write_then_read", w.ok and r2.value == "baseline write",
                        write=w.as_dict(), read=r2.as_dict(), app_version="old",
                        expected={"value": "baseline write"},
                        observed={"ok": w.ok and r2.ok, "value": r2.value,
                                  "error": w.error or r2.error})
        c = server.create_customer("baseline-new@example.test", "created in baseline")
        r3 = server.read_note(c.value) if c.ok else c
        ok &= log.check("old.create_then_read", c.ok and r3.value == "created in baseline",
                        create=c.as_dict(), read=r3.as_dict(), app_version="old",
                        expected={"value": "created in baseline"},
                        observed={"ok": c.ok and r3.ok, "value": r3.value if c.ok else None,
                                  "error": c.error or r3.error})
        db_note = db.query(run_db, "SELECT note FROM account_user WHERE id=%s", (pk,))[0][0]
        ok &= log.check("db.note_column_written", db_note == "baseline write", value=db_note,
                        app_version="database", expected={"note": "baseline write"},
                        observed={"note": db_note})
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
    log.set_step(f"pytest_{version}")
    log.scope.update(schema_state=None, database_snapshot_id=None,
                     app_commits={"old" if version == "old" else "new": scope.worktree_head(version)})
    return log.check(f"pytest.{version}", proc.returncode == 0, provenance=MANUAL,
                     tests=PYTEST_SUBSET, counts=counts, exit=proc.returncode,
                     seconds=round(time.monotonic() - t0, 1),
                     app_version="old" if version == "old" else "new",
                     expected={"exit": 0}, observed={"exit": proc.returncode, "counts": counts})


def run(log: RunLog, rebuild_seed: bool = False, skip_pytest: bool = False,
        seed_source: str = "synthetic", olist_dir=None, seed_limit: int | None = None) -> dict:
    script, provenance = prepare_seed(seed_source, olist_dir, seed_limit)
    if rebuild_seed or not seed_ready(seed_source, provenance["customers"]):
        build_seed(log, script, provenance)
    else:
        log.event("seed_reused", name=SEED_DB, **provenance)
    ok = smoke_old(log)
    if not skip_pytest:
        for version in PYTEST_VERSIONS:
            ok &= run_pytest(log, version)
    return {"ok": ok, "seed": {k: v for k, v in provenance.items() if k != "path"}}
