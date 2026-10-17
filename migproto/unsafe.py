"""Reproduce: immediate column rename succeeds as a migration but breaks the old app."""

import re

from . import db, scope, seed
from .config import PORTS, ROOT, SEED_DB
from .runlog import MANUAL, RunLog
from .saleor_app import AppServer, manage

MIGRATION = "0107_rename_user_note_customer_note"
NEW_VERSION = "new-unsafe"


def _server_errors(server: AppServer) -> list[str]:
    text = server.log_path.read_text(errors="replace") if server.log_path.exists() else ""
    return sorted(set(re.findall(r"[\w.]*(?:UndefinedColumn|ProgrammingError)[^\n]*", text)))


def _introspect(log: RunLog, dbname: str) -> dict:
    state = scope.schema_state(dbname)
    log.scope["schema_state"] = state
    return state


def run(log: RunLog) -> dict:
    log.event("scenario", provenance=MANUAL,
              msg="INTENTIONALLY INTRODUCED SCENARIO (not an upstream Saleor bug): "
                  "immediate rename of account_user.note -> customer_note")
    log.set_step("S0")
    run_db = db.new_run_db("unsafe")
    old = AppServer("old", "old", PORTS["old"], run_db, log.dir / "server-old.log")
    new = AppServer("new", NEW_VERSION, PORTS["new"], run_db, log.dir / "server-new.log")
    evidence: dict = {"run_db": run_db}
    try:
        log.scope["app_commits"] = {"old": scope.worktree_head("old"),
                                    "new": scope.worktree_head(NEW_VERSION)}
        state = _introspect(log, run_db)
        snap = scope.snapshot_id(run_db, state)
        log.scope["database_snapshot_id"] = snap
        log.start_states[log.attempt_id] = snap
        log.event("start_state", template_db=SEED_DB, run_db=run_db, database_snapshot_id=snap,
                  schema_state=state,
                  apps={"old": scope.app_identity("old"), "new": scope.app_identity(NEW_VERSION)})
        rows = seed.seed_rows(run_db, "note")
        pk = next(k for k, v in rows.items() if v)
        evidence["user_pk"] = pk

        old.start()
        pre = old.read_note(pk)
        log.check("pre.old_read_works", pre.ok and pre.value == rows[pk], result=pre.as_dict(),
                  app_version="old", expected={"ok": True, "value": rows[pk]},
                  observed={"ok": pre.ok, "value": pre.value, "error": pre.error})

        log.set_step("migrate_unsafe")
        sqlm = manage(NEW_VERSION, ["sqlmigrate", "account", MIGRATION], run_db)
        log.write("sql/unsafe_0107.sql", sqlm.stdout)
        evidence["migration_sql"] = sqlm.stdout.strip()

        mig = manage(NEW_VERSION, ["migrate", "account", MIGRATION], run_db,
                     log.dir / "migrate-new-unsafe.log")
        _introspect(log, run_db)
        log.check("migration_succeeds", mig.returncode == 0, exit=mig.returncode,
                  app_version="new", check_type="execution",
                  expected={"exit": 0}, observed={"exit": mig.returncode})
        cols = db.columns(run_db, "account_user")
        note_cols = [c for c in cols if "note" in c]
        log.check("schema.note_renamed", "customer_note" in cols and "note" not in cols,
                  note_cols=note_cols, app_version="database",
                  expected={"note_columns": ["customer_note"]},
                  observed={"note_columns": note_cols})

        log.set_step("post_migration")
        new.start()
        nw = new.write_note(pk, "written by NEW app after rename")
        nr = new.read_note(pk)
        log.check("new.write_then_read_works",
                  nw.ok and nr.ok and nr.value == "written by NEW app after rename",
                  operation="customerUpdate(note) + user{note} on new app",
                  write=nw.as_dict(), read=nr.as_dict(), app_version="new",
                  expected={"ok": True, "value": "written by NEW app after rename"},
                  observed={"ok": nw.ok and nr.ok, "value": nr.value,
                            "error": nw.error or nr.error})

        orr = old.read_note(pk)
        ow = old.write_note(pk, "written by OLD app after rename")
        oc = old.create_customer("old-after-rename@example.test", "old create")
        old_read_failed = not orr.ok
        old_write_failed = not ow.ok
        log.check("old.read_fails", old_read_failed, operation="query user(id){note} on old app",
                  result=orr.as_dict(), app_version="old",
                  expected={"ok": False}, observed={"ok": orr.ok, "error": orr.error})
        log.check("old.write_fails", old_write_failed,
                  operation="mutation customerUpdate(input:{note}) on old app", result=ow.as_dict(),
                  app_version="old", expected={"ok": False},
                  observed={"ok": ow.ok, "error": ow.error})
        log.event("observation", name="old.create_customer", result=oc.as_dict())
        anon = old._post("{ shop { name } }", None, None)
        log.event("observation", name="old.anonymous_shop_query",
                  ok=not anon.get("errors"), data=anon.get("data"),
                  msg="old app requests that never load a User row still work")
        evidence["old_anonymous_shop_query_ok"] = not anon.get("errors")
        server_errors = _server_errors(old)
        has_undefined = any("note" in e for e in server_errors)
        log.check("old.server_log_has_undefined_column", has_undefined,
                  errors=server_errors[:5], app_version="old",
                  expected={"undefined_column_logged": True},
                  observed={"undefined_column_logged": has_undefined})
        log.write("server-old-tail.log", old.log_tail(200))

        evidence.update(
            new_operation={"write": nw.as_dict(), "read": nr.as_dict()},
            old_read_error=orr.error,
            old_write_error=ow.error,
            old_create_error=oc.error,
            old_server_errors=server_errors[:5],
        )
        repro = (
            "#!/usr/bin/env bash\n"
            "# Reproduce the intentionally introduced unsafe rename (not an upstream bug).\n"
            f"set -euo pipefail\ncd {ROOT}\n"
            "uv run migproto setup && uv run migproto baseline --skip-pytest && uv run migproto unsafe\n"
        )
        log.write("reproduce.sh", repro)
        log.write_json("evidence.json", evidence)
        reproduced = (mig.returncode == 0 and nr.ok and old_read_failed and old_write_failed)
        return {"verdict": "REPRODUCED" if reproduced else "NOT_REPRODUCED", **evidence}
    finally:
        old.stop()
        new.stop()
        db.drop(run_db)
