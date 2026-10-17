"""migproto: feasibility prototype for a database migration compatibility reviewer."""

import argparse
import sys
import traceback

from .runlog import RunLog


def _run(command: str, fn) -> int:
    log = RunLog(command)
    try:
        result = fn(log)
    except Exception as exc:
        log.write("error.txt", traceback.format_exc())
        log.summary("ERROR", error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    verdict = result.pop("verdict", None) or ("PASS" if result.get("ok") else "FAIL")
    log.summary(verdict, **result)
    return 0 if verdict in ("PASS", "REPRODUCED") else 1


def cmd_setup(args) -> int:
    from . import setup_env
    return _run("setup", lambda log: {"ok": True, **setup_env.run(log)})


def cmd_baseline(args) -> int:
    from . import baseline
    return _run("baseline", lambda log: baseline.run(log, args.rebuild_seed, args.skip_pytest))


def cmd_unsafe(args) -> int:
    from . import unsafe
    return _run("unsafe", unsafe.run)


def cmd_staged(args) -> int:
    from . import plans, staged
    def go(log):
        plan = plans.load(args.plan)
        tests = plans.load(args.tests) if args.tests else None
        return staged.run(log, plan, tests, provenance=args.provenance)
    return _run("staged", go)


def cmd_agent_loop(args) -> int:
    from .agents import loop
    return _run(f"agent-loop-{args.proposals}", lambda log: loop.run(log, args.proposals))


def cmd_all(args) -> int:
    codes = {}
    codes["setup"] = cmd_setup(args)
    codes["baseline"] = cmd_baseline(args)
    codes["unsafe"] = cmd_unsafe(args)
    args.proposals = "scripted"
    codes["agent-loop-scripted"] = cmd_agent_loop(args)
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
    sub.add_parser("unsafe", help="reproduce old-app breakage from an immediate column rename")
    s = sub.add_parser("staged", help="execute one staged migration plan with compatibility checks")
    s.add_argument("--plan", default=str(PROPOSALS / "scripted" / "plan_v2.json"))
    s.add_argument("--tests", default=str(PROPOSALS / "scripted" / "tester.json"))
    s.add_argument("--provenance", default="scripted")
    a = sub.add_parser("agent-loop", help="investigator -> planner -> tester with one revision")
    a.add_argument("--proposals", choices=["scripted", "llm"], default="scripted")
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
