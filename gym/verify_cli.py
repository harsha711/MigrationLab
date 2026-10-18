"""Score one policy answer with the runner; the bridge from the NeMo Gym server process.

    echo '{"scenario_id": "add-table", "output_text": "{...plan...}", "model": "m"}' \
      | uv run python gym/verify_cli.py

Reads one JSON object on stdin and prints one JSON object (scoring.verify's result) as the
last stdout line; runner progress goes to stderr. Applies the Gym runner settings
(migration_review/env.py: ports 8081/8082, Redis DB 8, this worktree's lock) unless
--keep-env is given. Exit code 0 whenever a result was printed, 2 on bad input.
"""

import argparse
import contextlib
import json
import sys

from migration_review import env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep-env", action="store_true",
                    help="use MIGPROTO_* from the environment instead of the Gym settings")
    args = ap.parse_args(argv)
    if not args.keep_env:
        env.apply()
    try:
        req = json.loads(sys.stdin.read())
        scenario_id = req["scenario_id"]
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": f"bad request: {exc}"}))
        return 2
    from migration_review import scoring   # after env.apply: migproto.config reads env on import

    with contextlib.redirect_stdout(sys.stderr):
        result = scoring.verify(scenario_id, req.get("output_text"), model=req.get("model"))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
