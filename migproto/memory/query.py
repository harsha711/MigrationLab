"""Exact-scope queries over the evidence store.

Rules (docs/team/03-memory-ui.md, AGENTS.md evidence rules):
- Filters match exactly. A filter left as None does not filter. NULL (unknown) scope never
  matches a concrete filter value.
- Outcomes from different runs, attempts or scopes are returned side by side with their
  scope. They are never merged and never flagged as conflicts.

Note for agent use (Owner 2): `examples/` are frozen outcomes. Evaluated agents must not
query fixture runs (pass `source_kind="local_run"`).
"""

import json
import sqlite3

APP_ORDER = ["old", "new-staged", "new-unsafe"]


def _obj(value):
    return None if value is None else json.loads(value)


def _steps(conn, observation_id):
    return [dict(s) for s in conn.execute(
        "SELECT step, app_version, op, user_label AS user, ok, value, error "
        "FROM observation_steps WHERE observation_id = ? ORDER BY step", (observation_id,))]


def _observation(conn, row) -> dict:
    o = dict(row)
    o["schema_state"] = _obj(o.pop("schema_state_json"))
    o["raw"] = _obj(o.pop("raw_json"))
    o["steps"] = _steps(conn, o["observation_id"])
    o["app_versions"] = sorted({s["app_version"] for s in o["steps"] if s["app_version"]},
                               key=APP_ORDER.index)
    return o


def query_observations(conn: sqlite3.Connection, *, run_id=None, attempt_id=None, plan_id=None,
                       stage_id=None, check_name=None, status=None, app_version=None,
                       source_kind=None) -> list[dict]:
    """Exact-match filter. `app_version` matches checks with a step on that app version."""
    where, args = [], []
    for col, val in (("o.run_id", run_id), ("o.attempt_id", attempt_id), ("o.plan_id", plan_id),
                     ("o.stage_id", stage_id), ("o.check_name", check_name),
                     ("o.status", status), ("r.source_kind", source_kind)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    if app_version is not None:
        where.append("EXISTS (SELECT 1 FROM observation_steps s "
                     "WHERE s.observation_id = o.observation_id AND s.app_version = ?)")
        args.append(app_version)
    sql = ("SELECT o.* FROM observations o JOIN runs r USING (run_id)"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY o.run_id, o.seq")
    return [_observation(conn, row) for row in conn.execute(sql, args)]


def get_observation(conn, observation_id) -> dict | None:
    row = conn.execute("SELECT * FROM observations WHERE observation_id = ?",
                       (observation_id,)).fetchone()
    return _observation(conn, row) if row else None


def check_history(conn, check_name) -> list[dict]:
    """Every recorded outcome of one check, one row per scope, labeled, never merged."""
    return [{k: o[k] for k in ("observation_id", "run_id", "attempt_id", "plan_id", "stage_id",
                               "status", "outcome", "schema_state", "app_versions",
                               "scope_source")}
            for o in query_observations(conn, check_name=check_name)]


def evidence_for_claim(conn, claim_id) -> list[dict]:
    return [_observation(conn, row) for row in conn.execute(
        "SELECT o.* FROM claim_evidence ce JOIN observations o USING (observation_id) "
        "WHERE ce.claim_id = ? ORDER BY o.seq", (claim_id,))]


def list_runs(conn) -> list[dict]:
    rows = conn.execute("SELECT run_id, source_kind, command, verdict, elapsed_s, checks_total, "
                        "provenance, model FROM runs ORDER BY source_kind, run_id")
    return [dict(r) for r in rows]


def run_detail(conn, run_id) -> dict | None:
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run:
        return None
    run = dict(run)
    attempts = []
    for a in conn.execute("SELECT * FROM attempts WHERE run_id = ? ORDER BY attempt", (run_id,)):
        a = dict(a)
        a["failed"] = _obj(a.pop("failed_json"))
        aid = a["attempt_id"]
        a["plans"] = [{"plan_id": p["plan_id"], "file": p["file"], "body": _obj(p["body_json"]),
                       "sql": p["sql"]}
                      for p in conn.execute("SELECT * FROM plans WHERE attempt_id = ?", (aid,))]
        a["stages"] = [dict(s) for s in conn.execute(
            "SELECT stage_id, stage_kind, phase, description FROM stages "
            "WHERE attempt_id = ? ORDER BY seq", (aid,))]
        counts = conn.execute(
            "SELECT COUNT(*) AS total, SUM(status = 'FAIL') AS failed FROM observations "
            "WHERE attempt_id = ?", (aid,)).fetchone()
        a["checks_total"], a["checks_failed"] = counts["total"], counts["failed"] or 0
        a["claims"] = [dict(c) for c in conn.execute(
            "SELECT claim_id, role, kind, text, provenance, structured FROM claims "
            "WHERE attempt_id = ? ORDER BY claim_id", (aid,))]
        attempts.append(a)
    unattached_claims = [dict(c) for c in conn.execute(
        "SELECT claim_id, role, kind, text, provenance, structured FROM claims "
        "WHERE run_id = ? AND attempt_id IS NULL ORDER BY claim_id", (run_id,))]
    notes = [_obj(e["raw_json"]) for e in conn.execute(
        "SELECT raw_json FROM events WHERE run_id = ? AND kind IN ('observation', 'scenario') "
        "ORDER BY seq", (run_id,))]
    return {
        "run_id": run_id,
        "source_kind": run["source_kind"],
        "meta": _obj(run["meta_json"]),
        "summary": _obj(run["summary_json"]),
        "manifest": _obj(run["manifest_json"]),
        "attempts": attempts,
        "claims_without_attempt": unattached_claims,
        "runner_notes": notes,
        "results": results_panel(conn, run_id),
    }


def matrix(conn, run_id, attempt_id) -> dict:
    """Old/new compatibility matrix for one attempt of one run.

    Rows: app-level cases (checks with steps), in execution order, with their stage.
    Columns: writer -> reader, from the first and last step's app version.
    Each case fills exactly one column; every other column reads "not tested".
    Database-level checks (no steps, no app version) are listed per stage separately.
    """
    obs = query_observations(conn, run_id=run_id, attempt_id=attempt_id)
    columns, rows, db_checks = [], [], []
    for o in obs:
        apps = [s["app_version"] for s in o["steps"]]
        if not apps:
            db_checks.append({"observation_id": o["observation_id"], "stage_id": o["stage_id"],
                              "check_name": o["check_name"], "status": o["status"]})
            continue
        col = f"{apps[0] or 'unknown'} -> {apps[-1] or 'unknown'}"
        if col not in columns:
            columns.append(col)
        rows.append({"observation_id": o["observation_id"], "stage_id": o["stage_id"],
                     "check_name": o["check_name"], "status": o["status"], "column": col,
                     "path": apps, "definition_provenance": o["definition_provenance"]})
    rank = lambda c: tuple(APP_ORDER.index(p) if p in APP_ORDER else 99
                           for p in c.split(" -> "))
    columns.sort(key=rank)
    return {"run_id": run_id, "attempt_id": attempt_id, "columns": columns, "rows": rows,
            "db_checks": db_checks, "empty_cell": "not tested"}


def results_panel(conn, run_id) -> dict:
    """Measurements from saved data only. Absent fields are None ("not recorded")."""
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    meta = _obj(run["meta_json"]) or {}
    calls = _obj(run["model_calls_json"]) or []
    per_attempt = [dict(r) for r in conn.execute(
        "SELECT a.attempt_id, a.status, a.ok, "
        "  (SELECT COUNT(*) FROM observations o WHERE o.attempt_id = a.attempt_id) AS checks, "
        "  (SELECT COUNT(*) FROM observations o WHERE o.attempt_id = a.attempt_id "
        "     AND o.status = 'FAIL') AS failed_or_refused "
        "FROM attempts a WHERE a.run_id = ? ORDER BY a.attempt", (run_id,))]
    usage = [c.get("usage") or {} for c in calls]
    total = lambda key: sum(u.get(key) or 0 for u in usage) if calls else None
    return {
        "verdict": meta.get("verdict"),
        "elapsed_s": meta.get("elapsed_s"),
        "per_attempt": per_attempt,
        "model": meta.get("model"),
        "base_url": meta.get("base_url"),
        "checkpoint_revision": meta.get("checkpoint_revision"),
        "serving_backend": meta.get("serving_backend"),
        "hardware": meta.get("hardware"),
        "dev_only_model": meta.get("dev_only_model"),
        "model_calls": [{"role": c.get("role"), "seconds": c.get("seconds"),
                         "total_tokens": (c.get("usage") or {}).get("total_tokens"),
                         "error": c.get("error")} for c in calls],
        "model_call_count": len(calls) if calls else None,
        "model_seconds": round(sum(c.get("seconds") or 0 for c in calls), 1) if calls else None,
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"),
        "total_tokens": total("total_tokens"),
        "gym": None,  # NeMo Gym results not connected (gym/results/ does not exist)
    }
