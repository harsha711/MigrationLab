"""Smoke test: run Gym verification on one scenario with a scripted plan (no model).

    uv run python gym/smoke.py                         # add-table, scripted accept plan
    uv run python gym/smoke.py --scenario drop-column \
        --plan proposals/scripted/drop-column/safe.json --recommendation staged_rollout

Starts Saleor through the runner on ports 8081/8082 with Redis DB 8 and this worktree's lock
(migration_review/env.py). Needs the runner setup (`uv run migproto setup` and
`uv run migproto baseline --skip-pytest`). The scripted plan stands in for a policy answer:
this checks the plumbing, it is not a policy result. Exit 0 when verify returned a reward.
"""

import argparse
import json
import sys
from pathlib import Path

from migration_review import env

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="add-table")
    ap.add_argument("--plan", default="proposals/scripted/add-table/accept.json")
    ap.add_argument("--recommendation", default="accept_as_is",
                    help="recommendation added to the scripted plan (they carry none)")
    args = ap.parse_args(argv)
    settings = env.apply()
    print("runner settings:", json.dumps(settings), flush=True)
    from migration_review import scoring

    plan = json.loads((ROOT / args.plan).read_text())
    plan.pop("provenance", None)
    plan["recommendation"] = args.recommendation
    result = scoring.verify(args.scenario, json.dumps(plan), model="scripted-smoke")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] not in ("no_plan_json", "invalid_plan",
                                         "runner_exception") else 1


if __name__ == "__main__":
    sys.exit(main())
