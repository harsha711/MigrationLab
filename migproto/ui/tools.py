"""Pre-runner agent tools recorded in a run directory (configuration K and later), read-only.

Reads the files the controller writes when MIGPROTO_SCHEMA_PROFILE / MIGPROTO_PREFLIGHT are on:
`agents/schema_profile.json`, `agents/preflight-<n>.json`, the `tool`, `proposal` (with
`preflight_fix`) and `preflight_fix_invalid` events, and the planner files
`agents/planner-<n>.json` / `agents/planner-<n>-preflight.json`. Older runs have none of them
and get empty results. Nothing here is a verdict: a dry run executes on a throwaway copy of the
start state, without application servers, and only the runner's checks pass or fail.
"""

import difflib
import json
import re
from pathlib import Path

PREFLIGHT_FILE = re.compile(r"preflight-(\d+)\.json\Z")


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    out = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a line still being written
        if isinstance(event, dict):
            out.append(event)
    return out


def _stage_sql(plan) -> list[str]:
    """Plan stages as diffable text lines: a header per stage, then its SQL statements."""
    lines = []
    for stage in (plan or {}).get("stages") or []:
        if not isinstance(stage, dict):
            continue
        lines.append(f"-- stage {stage.get('id')} ({stage.get('kind')}, {stage.get('phase')})")
        sql = stage.get("sql") or []
        lines += [str(s) for s in (sql if isinstance(sql, list) else [sql])]
    return lines


def plan_diff(parent, fixed) -> list[str] | None:
    if not isinstance(parent, dict) or not isinstance(fixed, dict):
        return None
    return list(difflib.unified_diff(_stage_sql(parent), _stage_sql(fixed), "before dry run",
                                     "after dry run", lineterm="", n=2))


def run_tools(run_dir: Path) -> dict:
    """Schema profile, per-attempt dry runs and planner fixes, and the tool events of one run."""
    run_dir = Path(run_dir)
    agents = run_dir / "agents"
    events = _events(run_dir)
    attempts: dict[int, dict] = {}

    def slot(n) -> dict:
        return attempts.setdefault(int(n), {"attempt": int(n), "report": None, "event": None,
                                            "fix": None, "fix_invalid": None})

    if agents.is_dir():
        for path in agents.iterdir():
            m = PREFLIGHT_FILE.match(path.name)
            if m:
                slot(m.group(1))["report"] = _json(path)
    for e in events:
        n = e.get("attempt")
        if not isinstance(n, int):
            continue
        if e.get("kind") == "tool" and e.get("name") == "preflight":
            slot(n)["event"] = {k: e.get(k) for k in ("plan_id", "first_error", "t")}
        elif e.get("kind") == "proposal" and e.get("preflight_fix"):
            fix = {"parent_plan_id": e.get("parent_plan_id"), "plan_id": e.get("plan_id"),
                   "diff": plan_diff(_json(agents / f"planner-{n}.json"),
                                     _json(agents / f"planner-{n}-preflight.json"))}
            slot(n)["fix"] = fix
        elif e.get("kind") == "preflight_fix_invalid":
            slot(n)["fix_invalid"] = {"plan_id": e.get("plan_id"), "error": e.get("error")}
    tool_events = [{k: e.get(k) for k in ("t", "name", "msg", "attempt")}
                   for e in events if e.get("kind") == "tool"]
    return {"schema_profile": _json(agents / "schema_profile.json"),
            "attempts": [attempts[n] for n in sorted(attempts)],
            "tool_events": tool_events}


def summary(run_dir: Path) -> dict | None:
    """Counts for batch and compare views; None when the run used no dry run."""
    attempts = run_tools(run_dir)["attempts"]
    if not attempts:
        return None

    def first_error(a):
        return (a["report"] or {}).get("first_error") or (a["event"] or {}).get("first_error")

    return {"dry_runs": len(attempts),
            "errors_found": sum(1 for a in attempts if first_error(a)),
            "fixes": sum(1 for a in attempts if a["fix"]),
            "fixes_invalid": sum(1 for a in attempts if a["fix_invalid"])}
