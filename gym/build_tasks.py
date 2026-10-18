"""Freeze the Gym tasks: one line per scenario in gym/tasks/migration_review.jsonl.

    uv run python gym/build_tasks.py           # rewrite the frozen file
    uv run python gym/build_tasks.py --check   # exit 1 if the frozen file is out of date

Needs the scenario OLD worktrees under vendor/ (the deterministic code search reads them).
Reads no scripted plans, expected verdicts, patches or examples.
"""

import argparse
import sys

from migration_review import tasks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    rows = tasks.build_all()
    if args.check:
        frozen = tasks.load()
        stale = sorted({r["task_id"] for r in rows} ^ {r["task_id"] for r in frozen}
                       | {r["task_id"] for r, f in zip(rows, frozen) if r != f})
        print("up to date" if not stale else f"stale: {stale}")
        return 1 if stale else 0
    path = tasks.write(rows)
    print(f"wrote {len(rows)} tasks to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
