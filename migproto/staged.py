"""Execute a staged migration plan against two live app versions; decide pass/fail here."""

import time

from . import checks, db, plans
from .config import PORTS
from .runlog import MANUAL, RunLog
from .saleor_app import AppServer, manage, port_open

NEW_VERSION = "new-staged"
STATE_MIGRATION = "0107_rename_user_note_customer_note"


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
        log.check(f"stage.{stage['id']}.executes", False, provenance=prov, error=str(exc))
        return False, results
    log.check(f"stage.{stage['id']}.executes", True, provenance=prov,
              batches=len(results) if stage.get("repeat_until_zero_rows") else None)
    return True, results


def _fails(log: RunLog, start: int) -> int:
    return sum(c["status"] == "FAIL" for c in log.checks[start:])


def run(log: RunLog, plan: dict, tests: dict | None, provenance: str = "scripted",
        keep_db: bool = False) -> dict:
    start = len(log.checks)
    try:
        plans.validate_plan(plan)
        plans.validate_tests(tests)
    except plans.PlanError as exc:
        log.check("plan.schema_valid", False, provenance=provenance, error=str(exc))
        return {"ok": False, "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
                "error": str(exc)}
    log.check("plan.schema_valid", True, provenance=provenance, plan_id=plan["plan_id"])
    log.write_json(f"plan-{plan['plan_id']}.json", plan)
    sql_dump = []
    for st in plan["stages"]:
        if st["kind"] == "sql":
            sql_dump.append(f"-- stage {st['id']} ({st['phase']})"
                            + (" [repeat until 0 rows]" if st.get("repeat_until_zero_rows") else ""))
            sql_dump.extend(s.rstrip().rstrip(";") + ";" for s in st["sql"])
    log.write(f"sql/plan-{plan['plan_id']}.sql", "\n".join(sql_dump) + "\n")

    dbname = db.new_run_db(f"staged_{plan['plan_id']}".lower()[:30])
    old = AppServer("old", "old", PORTS["old"], dbname, log.dir / f"server-old-{plan['plan_id']}.log")
    new = AppServer("new", NEW_VERSION, PORTS["new"], dbname,
                    log.dir / f"server-new-{plan['plan_id']}.log")
    servers = {"old": old, "new": new}
    stage_log = []
    halted = None
    try:
        old.start()
        ctx = checks.Context.create(log, dbname, servers)
        checks.app_smoke(ctx, "old", "S0")
        checks.preservation(ctx, "note", "S0")

        for st in plan["stages"]:
            sid, kind = st["id"], st["kind"]
            t0 = time.monotonic()
            log.event("stage", provenance=provenance, stage=sid, stage_kind=kind,
                      phase=st.get("phase"), description=st.get("description"))
            failures_before = _fails(log, start)

            if kind == "sql" and st["phase"] == "contract":
                if failures_before:
                    halted = f"contract stage {sid} skipped: {failures_before} earlier check failures"
                    log.check(f"stage.{sid}.preconditions", False, msg=halted)
                    break
                old_alive = old.alive() or port_open(PORTS["old"])
                if not log.check("retirement_gate", not old_alive, stage=sid,
                                 msg="old app must be stopped before contract SQL runs",
                                 old_alive=old_alive):
                    halted = f"contract stage {sid} refused: old app still running"
                    break

            if kind == "sql":
                ok, _ = _exec_sql_stage(log, dbname, st, provenance)
                if not ok:
                    halted = f"stage {sid} failed to execute"
                    break
                cols = db.columns(dbname, "account_user")
                log.event("schema", stage=sid, note_columns=[c for c in cols if "note" in c],
                          triggers=db.triggers(dbname, "account_user"))
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
                        log.check(f"{sid}.both_columns_present", False, columns=cols)
                elif st["phase"] == "contract":
                    log.check(f"{sid}.old_column_removed", "note" not in cols,
                              note_columns=[c for c in cols if "note" in c])
                    if "customer_note" in cols:
                        checks.preservation(ctx, "customer_note", sid)
                    if new.alive():
                        checks.app_smoke(ctx, "new", sid)

            elif kind == "deploy_new":
                sqlm = manage(NEW_VERSION, ["sqlmigrate", "account", STATE_MIGRATION], dbname)
                log.write("sql/new-staged-0107-sqlmigrate.sql", sqlm.stdout)
                log.check(f"{sid}.django_migration_is_state_only",
                          "ALTER" not in sqlm.stdout.upper(), provenance=MANUAL,
                          sqlmigrate=sqlm.stdout.strip())
                mig = manage(NEW_VERSION, ["migrate", "account", STATE_MIGRATION], dbname,
                             log.dir / f"migrate-new-staged-{plan['plan_id']}.log")
                if not log.check(f"{sid}.django_migrate", mig.returncode == 0, exit=mig.returncode):
                    halted = "django migrate failed"
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
                log.check(f"{sid}.old_unreachable", not port_open(PORTS["old"]),
                          msg="old app process stopped and port closed")
                if new.alive():
                    checks.app_smoke(ctx, "new", sid)

            failed_here = _fails(log, start) - failures_before
            stage_log.append({"stage": sid, "kind": kind, "phase": st.get("phase"),
                              "failed_checks": failed_here,
                              "seconds": round(time.monotonic() - t0, 1)})

        completed = halted is None and len(stage_log) == len(plan["stages"])
        if completed:
            demo = AppServer("old", "old", PORTS["old"], dbname,
                             log.dir / f"server-old-after-contract-{plan['plan_id']}.log")
            demo.start()
            pk = next(iter(k for k in ctx.expected if ctx.expected[k]))
            r = demo.read_note(pk)
            log.event("observation", name="retired_old_after_contract", ok=r.ok, error=r.error,
                      msg="expected failure: old code cannot run after contract (why the gate exists)")
            demo.stop()
        failed = [c for c in log.checks[start:] if c["status"] == "FAIL"]
        return {
            "ok": completed and not failed,
            "plan_id": plan["plan_id"],
            "completed_all_stages": completed,
            "halted": halted,
            "stages": stage_log,
            "failed_checks": [{"name": c["name"], "steps": c.get("steps"), "error": c.get("error"),
                               "examples": c.get("examples")} for c in failed],
            "run_db": dbname,
        }
    finally:
        old.stop()
        new.stop()
        if not keep_db:
            db.drop(dbname)
