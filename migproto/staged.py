"""Execute a staged migration plan against two live app versions; decide pass/fail here.

Callers outside the runner use `runner_api.execute_plan`, which wraps `run`.
"""

import time

from . import checks, db, plans, scope
from .config import PORTS, SEED_DB
from .runlog import MANUAL, SCOPE_KEYS, RunLog
from .saleor_app import AppServer, manage, port_open

NEW_VERSION = "new-staged"
STATE_MIGRATION = "0107_rename_user_note_customer_note"
FAILED_OUTCOMES = ("fail", "refused", "error")


def _exec_sql_stage(log: RunLog, dbname: str, stage: dict, prov: str) -> tuple[bool, list]:
    statements = stage["sql"]
    results = []
    try:
        if stage.get("repeat_until_zero_rows"):
            for i in range(10_000):
                batch = db.run_guarded(dbname, statements)
                results.append({"iteration": i, "rowcounts": [r["rowcount"] for r in batch]})
                if all(r["rowcount"] == 0 for r in batch):
                    break
            else:
                raise RuntimeError("backfill did not converge in 10000 iterations")
        else:
            results = db.run_guarded(dbname, statements)
    except Exception as exc:
        log.check(f"stage.{stage['id']}.executes", False, provenance=prov, error=str(exc),
                  app_version="database", check_type="execution",
                  expected={"error": None}, observed={"error": str(exc)})
        return False, results
    batches = len(results) if stage.get("repeat_until_zero_rows") else None
    log.check(f"stage.{stage['id']}.executes", True, provenance=prov, batches=batches,
              app_version="database", check_type="execution",
              expected={"error": None}, observed={"error": None, "batches": batches})
    return True, results


def _fails(log: RunLog, start: int) -> int:
    return sum(c["status"] == "FAIL" for c in log.checks[start:])


def _introspect(log: RunLog, dbname: str) -> dict:
    state = scope.schema_state(dbname)
    log.scope["schema_state"] = state
    return state


def feedback_view(record: dict) -> dict:
    """A failed observation without the repeated scope keys (for results and planner feedback)."""
    return {k: v for k, v in record.items() if k not in SCOPE_KEYS}


def _start_state(log: RunLog, dbname: str) -> str | None:
    """Record app identity and the starting snapshot. Returns an error message if this
    attempt does not start from the same snapshot as earlier attempts in the run."""
    log.scope["app_commits"] = {"old": scope.worktree_head("old"),
                                "new": scope.worktree_head(NEW_VERSION)}
    state = _introspect(log, dbname)
    snap = scope.snapshot_id(dbname, state)
    log.scope["database_snapshot_id"] = snap
    earlier = {a: s for a, s in log.start_states.items() if a != log.attempt_id}
    log.start_states[log.attempt_id] = snap
    log.event("start_state", template_db=SEED_DB, run_db=dbname, database_snapshot_id=snap,
              schema_state=state,
              apps={"old": scope.app_identity("old"), "new": scope.app_identity(NEW_VERSION)})
    differing = sorted(a for a, s in earlier.items() if s != snap)
    log.check("start_state.equivalent", not differing, app_version="database",
              check_type="execution",
              expected={"database_snapshot_id": sorted(set(earlier.values())) or [snap]},
              observed={"database_snapshot_id": snap, "differs_from": differing})
    if differing:
        return f"start state {snap} differs from earlier attempts {differing}"
    return None


def run(log: RunLog, plan: dict, tests: dict | None, provenance: str = "scripted",
        keep_db: bool = False) -> dict:
    start = len(log.checks)
    log.set_step("validate")
    try:
        plans.validate_plan(plan)
        plans.validate_tests(tests)
    except plans.PlanError as exc:
        log.check("plan.schema_valid", False, provenance=provenance, error=str(exc),
                  check_type="safety_gate", expected={"valid": True},
                  observed={"valid": False, "error": str(exc)})
        return {"ok": False, "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
                "error": str(exc), "halted": f"plan rejected: {exc}", "halted_step": "validate",
                "completed_all_stages": False, "stages": [],
                "failed_checks": [feedback_view(c) for c in log.checks[start:]
                                  if c["outcome"] in FAILED_OUTCOMES],
                "run_db": None, "db_dropped": None}
    log.check("plan.schema_valid", True, provenance=provenance, plan_id=plan["plan_id"],
              check_type="safety_gate", expected={"valid": True}, observed={"valid": True})
    log.write_json(f"plan-{plan['plan_id']}.json", plan)
    sql_dump = []
    for st in plan["stages"]:
        if st["kind"] == "sql":
            sql_dump.append(f"-- stage {st['id']} ({st['phase']})"
                            + (" [repeat until 0 rows]" if st.get("repeat_until_zero_rows") else ""))
            sql_dump.extend(s.rstrip().rstrip(";") + ";" for s in st["sql"])
    log.write(f"sql/plan-{plan['plan_id']}.sql", "\n".join(sql_dump) + "\n")

    log.set_step("S0")
    dbname = db.new_run_db(f"staged_{plan['plan_id']}".lower()[:30])
    old = AppServer("old", "old", PORTS["old"], dbname, log.dir / f"server-old-{plan['plan_id']}.log")
    new = AppServer("new", NEW_VERSION, PORTS["new"], dbname,
                    log.dir / f"server-new-{plan['plan_id']}.log")
    servers = {"old": old, "new": new}
    stage_log = []
    halted = halted_step = None
    try:
        halted = _start_state(log, dbname)
        if halted:
            halted_step = "S0"
        else:
            old.start()
            ctx = checks.Context.create(log, dbname, servers)
            checks.app_smoke(ctx, "old", "S0")
            checks.preservation(ctx, "note", "S0")

        for st in plan["stages"] if not halted else []:
            sid, kind = st["id"], st["kind"]
            t0 = time.monotonic()
            log.set_step(sid)
            log.event("stage", provenance=provenance, stage=sid, stage_kind=kind,
                      phase=st.get("phase"), description=st.get("description"))
            failures_before = _fails(log, start)

            if kind == "sql" and st["phase"] == "contract":
                if failures_before:
                    halted = f"contract stage {sid} skipped: {failures_before} earlier check failures"
                    log.check(f"stage.{sid}.preconditions", False, msg=halted,
                              app_version="database", check_type="safety_gate",
                              expected={"earlier_failures": 0},
                              observed={"earlier_failures": failures_before})
                    halted_step = sid
                    break
                old_alive = old.alive() or port_open(PORTS["old"])
                if not log.check("retirement_gate", not old_alive, stage=sid,
                                 msg="old app must be stopped before contract SQL runs",
                                 old_alive=old_alive, app_version="old",
                                 check_type="safety_gate", expected={"old_alive": False},
                                 observed={"old_alive": old_alive}):
                    halted = f"contract stage {sid} refused: old app still running"
                    halted_step = sid
                    break

            if kind == "sql":
                ok, _ = _exec_sql_stage(log, dbname, st, provenance)
                if not ok:
                    halted = f"stage {sid} failed to execute"
                    halted_step = sid
                    break
                state = _introspect(log, dbname)
                cols = db.columns(dbname, "account_user")
                log.event("schema", stage=sid, note_columns=state["note_columns"],
                          triggers=state["triggers"], schema_state=state)
                both = "note" in cols and "customer_note" in cols
                if st["phase"] == "expand":
                    if old.alive():
                        checks.app_smoke(ctx, "old", sid)
                    checks.preservation(ctx, "note", sid)
                    if both and new.alive():
                        checks.columns_agree(ctx, sid)
                elif st["phase"] == "backfill":
                    if both:
                        checks.columns_agree(ctx, sid)
                        checks.preservation(ctx, "customer_note", sid)
                    else:
                        log.check(f"{sid}.both_columns_present", False, columns=cols,
                                  app_version="database",
                                  expected={"columns": ["note", "customer_note"]},
                                  observed={"columns": [c for c in cols if "note" in c]})
                elif st["phase"] == "contract":
                    note_cols = [c for c in cols if "note" in c]
                    log.check(f"{sid}.old_column_removed", "note" not in cols,
                              note_columns=note_cols, app_version="database",
                              expected={"note_present": False},
                              observed={"note_present": "note" in cols})
                    if "customer_note" in cols:
                        checks.preservation(ctx, "customer_note", sid)
                    if new.alive():
                        checks.app_smoke(ctx, "new", sid)

            elif kind == "deploy_new":
                sqlm = manage(NEW_VERSION, ["sqlmigrate", "account", STATE_MIGRATION], dbname)
                log.write("sql/new-staged-0107-sqlmigrate.sql", sqlm.stdout)
                state_only = "ALTER" not in sqlm.stdout.upper()
                log.check(f"{sid}.django_migration_is_state_only", state_only,
                          provenance=MANUAL, sqlmigrate=sqlm.stdout.strip(), app_version="new",
                          expected={"alters_schema": False},
                          observed={"alters_schema": not state_only})
                mig = manage(NEW_VERSION, ["migrate", "account", STATE_MIGRATION], dbname,
                             log.dir / f"migrate-new-staged-{plan['plan_id']}.log")
                migrated = mig.returncode == 0
                _introspect(log, dbname)
                if not log.check(f"{sid}.django_migrate", migrated, exit=mig.returncode,
                                 app_version="new", check_type="execution",
                                 expected={"exit": 0}, observed={"exit": mig.returncode}):
                    halted = "django migrate failed"
                    halted_step = sid
                    break
                new.start()
                checks.app_smoke(ctx, "new", sid)
                if old.alive():
                    checks.app_smoke(ctx, "old", sid)
                selected = list(dict.fromkeys(checks.MANDATORY_AFTER_DEPLOY))
                for cid in selected:
                    checks.run_case(ctx, cid, checks.CATALOGUE[cid], provenance=MANUAL)
                tester_prov = (tests or {}).get("provenance", provenance)
                for cid in (tests or {}).get("checks", []):
                    if cid in checks.CATALOGUE and cid not in selected:
                        checks.run_case(ctx, cid, checks.CATALOGUE[cid], provenance=tester_prov)
                    elif cid not in checks.CATALOGUE:
                        log.event("tester_unknown_check", provenance=tester_prov, name=cid)
                for case in (tests or {}).get("custom_cases", []):
                    checks.run_case(ctx, f"tester.{case['id']}", case["steps"],
                                    provenance=tester_prov)
                if "customer_note" in db.columns(dbname, "account_user"):
                    checks.columns_agree(ctx, sid)
                    checks.preservation(ctx, "customer_note", sid)
                checks.preservation(ctx, "note", sid)

            elif kind == "retire_old":
                old.stop()
                time.sleep(0.5)
                closed = not port_open(PORTS["old"])
                log.check(f"{sid}.old_unreachable", closed,
                          msg="old app process stopped and port closed", app_version="old",
                          expected={"port_open": False}, observed={"port_open": not closed})
                _introspect(log, dbname)
                if new.alive():
                    checks.app_smoke(ctx, "new", sid)

            failed_here = _fails(log, start) - failures_before
            stage_log.append({"stage": sid, "kind": kind, "phase": st.get("phase"),
                              "failed_checks": failed_here,
                              "seconds": round(time.monotonic() - t0, 1)})

        done = {s["stage"] for s in stage_log}
        for st in plan["stages"]:
            if st["id"] not in done and st["id"] != halted_step:
                log.set_step(st["id"])
                log.skip(f"stage.{st['id']}.not_run", f"not run: {halted}",
                         app_version="database" if st["kind"] == "sql" else None)

        completed = halted is None and len(stage_log) == len(plan["stages"])
        if completed:
            log.set_step("post_contract")
            demo = AppServer("old", "old", PORTS["old"], dbname,
                             log.dir / f"server-old-after-contract-{plan['plan_id']}.log")
            demo.start()
            pk = next(iter(k for k in ctx.expected if ctx.expected[k]))
            r = demo.read_note(pk)
            log.event("observation", name="retired_old_after_contract", ok=r.ok, error=r.error,
                      msg="expected failure: old code cannot run after contract (why the gate exists)")
            demo.stop()
        failed = [c for c in log.checks[start:] if c["outcome"] in FAILED_OUTCOMES]
        return {
            "ok": completed and not failed,
            "plan_id": plan["plan_id"],
            "completed_all_stages": completed,
            "halted": halted,
            "halted_step": halted_step,
            "stages": stage_log,
            "failed_checks": [feedback_view(c) for c in failed],
            "run_db": dbname,
            "db_dropped": not keep_db,
        }
    finally:
        old.stop()
        new.stop()
        if not keep_db:
            db.drop(dbname)
