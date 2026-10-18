"""migproto: feasibility prototype for a database migration compatibility reviewer."""

import argparse
import sys
import traceback

from . import lock
from .runlog import RunLog


def _run(command: str, fn) -> int:
    try:
        lock.acquire(command)
    except lock.ExecutionBusy as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        log = RunLog(command)
        lock.update_owner(run_id=log.run_id)
        try:
            result = fn(log)
        except Exception as exc:
            log.write("error.txt", traceback.format_exc())
            log.summary("ERROR", error=str(exc))
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        verdict = result.pop("verdict", None) or ("PASS" if result.get("ok") else "FAIL")
        log.summary(verdict, **result)
        if verdict in ("PASS", "REPRODUCED"):
            return 0
        return 2 if verdict == "ERROR" else 1
    finally:
        lock.release()


def cmd_setup(args) -> int:
    from . import setup_env
    return _run("setup", lambda log: {"ok": True, **setup_env.run(log)})


def cmd_baseline(args) -> int:
    from . import baseline
    from .config import OLIST_DIR
    return _run("baseline", lambda log: baseline.run(
        log, args.rebuild_seed, args.skip_pytest,
        seed_source=getattr(args, "seed_source", "synthetic"),
        olist_dir=getattr(args, "olist_dir", None) or OLIST_DIR,
        seed_limit=getattr(args, "seed_limit", None)))


def cmd_unsafe(args) -> int:
    from . import unsafe
    return _run("unsafe", unsafe.run)


def cmd_staged(args) -> int:
    from . import plans, runner_api
    def go(log):
        plan = plans.load(args.plan)
        tests = plans.load(args.tests) if args.tests else None
        report = runner_api.execute_plan(
            plan, run_context=runner_api.RunContext(log, provenance=args.provenance,
                                                    keep_db=getattr(args, "keep_db", False)),
            extra_tests=tests, scenario=getattr(args, "scenario", None))
        result = report.as_dict()
        result["runner_verdict"] = result.pop("verdict")
        return result
    return _run("staged", go)


def cmd_agent_loop(args) -> int:
    from .agents import loop
    return _run(f"agent-loop-{args.proposals}", lambda log: loop.run(
        log, args.proposals, request=getattr(args, "request", None),
        topology=getattr(args, "topology", None)
    ))


def cmd_all(args) -> int:
    codes = {}
    try:
        with lock.execution_lock("all"):
            codes["setup"] = cmd_setup(args)
            codes["baseline"] = cmd_baseline(args)
            codes["unsafe"] = cmd_unsafe(args)
            args.proposals = "scripted"
            codes["agent-loop-scripted"] = cmd_agent_loop(args)
    except lock.ExecutionBusy as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("exit codes:", codes)
    return 0 if all(c == 0 for c in codes.values()) else 1


def cmd_ui(args) -> int:
    from .ui import server
    server.serve(port=args.port, db=args.db)
    return 0


def main(argv=None) -> int:
    from .config import PROPOSALS

    p = argparse.ArgumentParser(prog="migproto", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="start services, clone pinned Saleor, worktrees, venv")
    b = sub.add_parser("baseline", help="seed template DB, smoke old app, run pytest subset")
    b.add_argument("--rebuild-seed", action="store_true")
    b.add_argument("--skip-pytest", action="store_true")
    b.add_argument("--seed-source", choices=["synthetic", "olist"], default="synthetic",
                   help="customer seed: 200 synthetic notes (default) or Olist review comments")
    b.add_argument("--olist-dir", help="unzipped Kaggle Olist dataset (or MIGPROTO_OLIST_DIR)")
    b.add_argument("--seed-limit", type=int, help="olist: first N customers (default: all)")
    sub.add_parser("unsafe", help="reproduce old-app breakage from an immediate column rename")
    s = sub.add_parser("staged", help="execute one staged migration plan with compatibility checks")
    s.add_argument("--plan", default=str(PROPOSALS / "scripted" / "plan_v2.json"))
    s.add_argument("--tests", default=str(PROPOSALS / "scripted" / "tester.json"))
    s.add_argument("--provenance", default="scripted")
    s.add_argument("--scenario", default="rename-note",
                   help="schema change under review (see migproto/scenarios.py)")
    s.add_argument("--keep-db", action="store_true",
                   help="keep the migproto_run_* database after the run, for inspection "
                        "(drop it by hand afterwards)")
    a = sub.add_parser("agent-loop", help="investigator -> planner -> tester with one revision")
    a.add_argument("--proposals", choices=["scripted", "llm"], default="scripted")
    a.add_argument("--request", help="path to a migproto.contracts/v1 review request JSON")
    a.add_argument("--topology", choices=["roles", "single"],
                   help="override the request's agent topology")
    al = sub.add_parser("all", help="setup, baseline, unsafe, scripted agent loop")
    al.add_argument("--rebuild-seed", action="store_true")
    al.add_argument("--skip-pytest", action="store_true")
    u = sub.add_parser("ui", help="read-only Replay Recorded Run screen over examples/ and runs/")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--db", default=None, help="evidence store path (default data/memory.sqlite)")
    args = p.parse_args(argv)
    handlers = {"setup": cmd_setup, "baseline": cmd_baseline, "unsafe": cmd_unsafe,
                "staged": cmd_staged, "agent-loop": cmd_agent_loop, "all": cmd_all,
                "ui": cmd_ui}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
