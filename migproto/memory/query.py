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
import re
import sqlite3
from pathlib import Path

APP_ORDER = ["old", "new-staged", "new-unsafe"]
BATCH_PREFIX = "brev-batch-"
BATCH_VERDICTS = ("PASS", "FAIL", "INCOMPLETE", "ERROR")
_BATCH_ID = re.compile(r"brev-batch-[\w.-]+")
_RUN_ID = re.compile(r"\w[\w.-]*")  # one path component, never "." or ".."
# Run stamps in INFRA-NOTE: "20261017-192959" or, after a full one, the short form "-193746".
_INFRA_STAMP = re.compile(r"(?<!\d)(\d{8})?-(\d{6})(?!\d)")


def _obj(value):
    return None if value is None else json.loads(value)


def _steps(conn, observation_id):
    return [dict(s) for s in conn.execute(
        "SELECT step, app_version, op, user_label AS user, ok, value, error "
        "FROM observation_steps WHERE observation_id = ? ORDER BY step", (observation_id,))]


def _observation(conn, row) -> dict:
    o = dict(row)
    for key in ("schema_state", "raw", "app_commit", "expected", "observed"):
        o[key] = _obj(o.pop(f"{key}_json"))
    o["steps"] = _steps(conn, o["observation_id"])
    o["app_versions"] = sorted({s["app_version"] for s in o["steps"] if s["app_version"]},
                               key=APP_ORDER.index)
    return o


def query_observations(conn: sqlite3.Connection, *, run_id=None, attempt_id=None, plan_id=None,
                       stage_id=None, check_name=None, status=None, outcome=None,
                       check_type=None, app_scope=None, app_version=None,
                       source_kind=None) -> list[dict]:
    """Exact-match filter.

    `app_version` matches checks with a step on that app version (old / new-staged /
    new-unsafe). `app_scope` matches the runner's v1 `app_version` field (old / new / both /
    database); v0 checks have no app_scope and never match it.
    """
    where, args = [], []
    for col, val in (("o.run_id", run_id), ("o.attempt_id", attempt_id), ("o.plan_id", plan_id),
                     ("o.stage_id", stage_id), ("o.check_name", check_name),
                     ("o.status", status), ("o.outcome", outcome), ("o.check_type", check_type),
                     ("o.app_scope", app_scope), ("r.source_kind", source_kind)):
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
                               "app_scope", "database_snapshot_id", "scope_source")}
            for o in query_observations(conn, check_name=check_name)]


def evidence_for_claim(conn, claim_id) -> list[dict]:
    return [_observation(conn, row) for row in conn.execute(
        "SELECT o.* FROM claim_evidence ce JOIN observations o USING (observation_id) "
        "WHERE ce.claim_id = ? ORDER BY o.seq", (claim_id,))]


def list_runs(conn) -> list[dict]:
    rows = conn.execute("SELECT run_id, source_kind, command, verdict, elapsed_s, checks_total, "
                        "provenance, model FROM runs ORDER BY source_kind, run_id")
    return [dict(r) for r in rows]


def _claims(conn, where: str, args: tuple) -> list[dict]:
    """Claims with their cited evidence (observation status / outcome when resolved)."""
    out = []
    for c in conn.execute("SELECT claim_id, role, kind, text, provenance, structured FROM claims "
                          f"WHERE {where} ORDER BY claim_id", args):
        c = dict(c)
        c["evidence"] = [dict(e) for e in conn.execute(
            "SELECT ce.observation_id, ce.resolved, o.check_name, o.status, o.outcome "
            "FROM claim_evidence ce LEFT JOIN observations o USING (observation_id) "
            "WHERE ce.claim_id = ? ORDER BY o.seq", (c["claim_id"],))]
        out.append(c)
    return out


def outcome_counts(conn, attempt_id) -> dict | None:
    """v1 outcome counts for one attempt; None when no observation recorded an outcome (v0)."""
    rows = conn.execute("SELECT outcome, COUNT(*) AS n FROM observations "
                        "WHERE attempt_id = ? AND outcome IS NOT NULL GROUP BY outcome",
                        (attempt_id,)).fetchall()
    return {r["outcome"]: r["n"] for r in rows} or None


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
                       "sql": p["sql"], "parent_plan_id": p["parent_plan_id"],
                       "solution_origin": p["solution_origin"]}
                      for p in conn.execute("SELECT * FROM plans WHERE attempt_id = ?", (aid,))]
        a["stages"] = [dict(s) for s in conn.execute(
            "SELECT stage_id, stage_kind, phase, description FROM stages "
            "WHERE attempt_id = ? ORDER BY seq", (aid,))]
        counts = conn.execute(
            "SELECT COUNT(*) AS total, SUM(status = 'FAIL') AS failed FROM observations "
            "WHERE attempt_id = ?", (aid,)).fetchone()
        a["checks_total"], a["checks_failed"] = counts["total"], counts["failed"] or 0
        a["outcome_counts"] = outcome_counts(conn, aid)
        a["claims"] = _claims(conn, "attempt_id = ?", (aid,))
        attempts.append(a)
    unattached_claims = _claims(conn, "run_id = ? AND attempt_id IS NULL", (run_id,))
    notes = [_obj(e["raw_json"]) for e in conn.execute(
        "SELECT raw_json FROM events WHERE run_id = ? AND kind IN ('observation', 'scenario') "
        "ORDER BY seq", (run_id,))]
    retrievals = [_obj(e["raw_json"]) for e in conn.execute(
        "SELECT raw_json FROM events WHERE run_id = ? AND kind = 'retrieval' ORDER BY seq",
        (run_id,))]
    return {
        "run_id": run_id,
        "source_kind": run["source_kind"],
        "meta": _obj(run["meta_json"]),
        "summary": _obj(run["summary_json"]),
        "manifest": _obj(run["manifest_json"]),
        "attempts": attempts,
        "claims_without_attempt": unattached_claims,
        "runner_notes": notes,
        "retrievals": retrievals,
        "results": results_panel(conn, run_id),
    }


def matrix(conn, run_id, attempt_id) -> dict:
    """Old/new compatibility matrix for one attempt of one run.

    Rows: app-level cases (checks with steps), in execution order, with their stage.
    Columns: writer -> reader, from the first and last step's app version, or "mixed" when
    a case starts and ends on one version but crosses to the other in between.
    Each case fills exactly one column; every other column reads "not tested".
    Database-level checks (no steps, no app version) are listed per stage separately.
    """
    obs = query_observations(conn, run_id=run_id, attempt_id=attempt_id)
    columns, rows, db_checks = [], [], []
    for o in obs:
        apps = [s["app_version"] for s in o["steps"]]
        if not apps:
            db_checks.append({"observation_id": o["observation_id"], "stage_id": o["stage_id"],
                              "check_name": o["check_name"], "status": o["status"],
                              "outcome": o["outcome"], "check_type": o["check_type"]})
            continue
        if apps[0] == apps[-1] and len(set(apps)) > 1:
            col = "mixed"  # starts and ends on one version but crosses to another in between
        else:
            col = f"{apps[0] or 'unknown'} -> {apps[-1] or 'unknown'}"
        if col not in columns:
            columns.append(col)
        rows.append({"observation_id": o["observation_id"], "stage_id": o["stage_id"],
                     "check_name": o["check_name"], "status": o["status"],
                     "outcome": o["outcome"], "column": col,
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
    for a in per_attempt:
        a["outcome_counts"] = outcome_counts(conn, a["attempt_id"])
    usage = [c.get("usage") or {} for c in calls]
    total = lambda key: sum(u.get(key) or 0 for u in usage) if calls else None
    totals = meta.get("agent_totals") or {}  # Owner 2 controller summary, preferred when present
    pick = lambda key, fallback: totals[key] if totals.get(key) is not None else fallback
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
        "model_call_count": pick("model_calls", len(calls) if calls else None),
        "model_seconds": pick("model_seconds", round(sum(c.get("seconds") or 0 for c in calls), 1)
                              if calls else None),
        "prompt_tokens": pick("prompt_tokens", total("prompt_tokens")),
        "completion_tokens": pick("completion_tokens", total("completion_tokens")),
        "total_tokens": pick("total_tokens", total("total_tokens")),
        "valid_proposal_rate": totals.get("valid_proposal_rate"),
        "budget": meta.get("budget"),
        "budget_remaining": totals.get("remaining"),
        "topology": meta.get("topology"),
        "reason": meta.get("reason"),
        "sampling": {k: meta.get(k) for k in ("temperature", "top_p", "reasoning_enabled")},
        "serving": {k: meta.get(k) for k in ("serving_version", "driver", "cuda",
                                             "brev_instance", "prefix_caching")},
        "contract_version": meta.get("contract_version"),
        "recorded_run_id": meta.get("recorded_run_id"),
        "database_snapshot_ids": sorted({s.get("database_snapshot_id") for s in
                                         meta.get("start_states") or []
                                         if s.get("database_snapshot_id")}) or None,
        "start_states": meta.get("start_states") or [],
        "gym": None,  # NeMo Gym results not connected (gym/results/ does not exist)
    }


# ---- Brev batches: read-only views of runs/brev-batch-*/ and their runs' files ----
#
# Batches are read from disk, not from the store: runs.jsonl, CONFIG-NOTE and INFRA-NOTE are
# supervisor records, not runner observations. Nothing here writes files or store rows.
# Expected verdicts (proposals/manual) are evaluation data and are never read here.

def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path):
    text = _read_text(path)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Records and the number of unreadable lines (a batch still running may end mid-line)."""
    records, bad = [], 0
    for line in (_read_text(path) or "").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            records.append(rec)
        else:
            bad += 1
    return records, bad


def infra_run_stamps(note: str | None, default_date: str | None = None) -> set[str]:
    """Run stamps (YYYYMMDD-HHMMSS) named in an INFRA-NOTE.

    A short "-HHMMSS" takes the date of the previous full stamp, else `default_date`.
    Batch directory names ("brev-batch-...") are not runs and are skipped.
    """
    stamps, date = set(), default_date
    for m in _INFRA_STAMP.finditer(note or ""):
        if (note or "")[max(0, m.start() - len(BATCH_PREFIX)):m.start()] == BATCH_PREFIX:
            continue
        date = m.group(1) or date
        if date:
            stamps.add(f"{date}-{m.group(2)}")
    return stamps


def _is_infra(run_id, stamps) -> bool:
    return bool(run_id) and any(run_id == s or run_id.startswith(s + "-") for s in stamps)


def _attempt_outcomes(summary: dict) -> list[dict] | None:
    """v1 outcome counts per attempt, every attempt kept in order; None when not recorded.

    A recorded but empty `outcome_counts` means no attempt executed and returns [].
    """
    counts = summary.get("outcome_counts")
    if not isinstance(counts, dict):
        return None

    def number(key):
        m = re.search(r"/a(\d+)$", key)
        return int(m.group(1)) if m else 10 ** 6

    return [{"attempt": key.rsplit("/", 1)[-1],
             **{k: (c or {}).get(k) for k in ("pass", "fail", "refused", "error", "skipped")}}
            for key, c in sorted(counts.items(), key=lambda kv: number(kv[0]))]


def _first(*values):
    return next((v for v in values if v is not None), None)


def _batch_run(rec: dict, runs_root: Path, stamps: set[str], stored: set[str]) -> dict:
    run_id = rec.get("run_id") if isinstance(rec.get("run_id"), str) else None
    run_dir = runs_root / run_id if run_id and _RUN_ID.fullmatch(run_id) else None
    summary = _read_json(run_dir / "summary.json") if run_dir else None
    s = summary if isinstance(summary, dict) else {}
    totals = s.get("agent_totals") or {}
    trace = _read_json(run_dir / "agents" / "trace.json") if run_dir else None
    url = trace.get("url") if isinstance(trace, dict) else None
    if not (isinstance(url, str) and url.startswith(("https://", "http://"))):
        url = None
    return {
        "n": rec.get("n"),
        "run_id": run_id,
        "scenario": _first(rec.get("scenario"), s.get("scenario")),
        "topology": _first(s.get("topology"), rec.get("topology")),
        "verdict": _first(s.get("verdict"), rec.get("verdict")),
        "verdict_source": "summary.json" if s.get("verdict") else "runs.jsonl",
        "recommendation": s.get("recommendation"),
        "recommendation_recorded": "recommendation" in s,  # absent (older runs) = unknown
        "runner_recommendation": s.get("runner_recommendation"),
        "recommendation_agrees": s.get("recommendation_agrees"),
        "attempts": _attempt_outcomes(s),
        "total_tokens": _first(totals.get("total_tokens"), rec.get("total_tokens")),
        "model_calls": _first(totals.get("model_calls"), rec.get("model_calls")),
        "wall_seconds": _first(totals.get("wall_seconds"), s.get("elapsed_s"), rec.get("seconds")),
        "stopped": _first(totals.get("exhausted_reason"), s.get("reason"), rec.get("reason")),
        "model": s.get("model"),
        "has_summary": bool(s),
        "in_store": run_id in stored,
        "trace_url": url,
        "infrastructure_failure": _is_infra(run_id, stamps),
    }


def _stored_run_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT run_id FROM runs")} if conn is not None else set()


def _batch(path: Path, runs_root: Path, stored: set[str]) -> dict:
    batch_id = path.name
    infra_note = _read_text(path / "INFRA-NOTE")
    m = re.match(r"brev-batch-(\d{8})-", batch_id)
    stamps = infra_run_stamps(infra_note, m.group(1) if m else None)
    records, bad = _read_jsonl(path / "runs.jsonl")
    runs = sorted((_batch_run(r, runs_root, stamps, stored) for r in records),
                  key=lambda r: (r["n"] if isinstance(r["n"], int) else 10 ** 6, r["run_id"] or ""))
    cells = {}
    for r in runs:
        if r["infrastructure_failure"]:
            continue
        cell = cells.setdefault((r["scenario"], r["topology"]), {
            "scenario": r["scenario"], "topology": r["topology"],
            **{v: 0 for v in BATCH_VERDICTS}, "other": {}})
        verdict = str(r["verdict"]).upper() if r["verdict"] else "not recorded"
        if verdict in BATCH_VERDICTS:
            cell[verdict] += 1
        else:
            cell["other"][verdict] = cell["other"].get(verdict, 0) + 1
    counts = sorted(cells.values(), key=lambda c: (c["scenario"] is None, c["scenario"] or "",
                                                   c["topology"] or ""))
    return {
        "batch_id": batch_id,
        "config_note": _read_text(path / "CONFIG-NOTE"),
        "infra_note": infra_note,
        "has_runs_jsonl": (path / "runs.jsonl").is_file(),
        "unreadable_lines": bad,
        "stop_file": (path / "STOP").exists(),
        "models": sorted({r["model"] for r in runs if r["model"]}),
        "run_count": len(runs),
        "model_result_count": sum(not r["infrastructure_failure"] for r in runs),
        "counts": counts,
        "infrastructure_failures": [r for r in runs if r["infrastructure_failure"]],
        "runs": runs,
    }


def _batch_dirs(runs_root: Path) -> list[Path]:
    if not runs_root.is_dir():
        return []
    return sorted((p for p in runs_root.glob(BATCH_PREFIX + "*")
                   if p.is_dir() and _BATCH_ID.fullmatch(p.name)),
                  key=lambda p: p.name, reverse=True)


def list_batches(runs_root, conn=None) -> list[dict]:
    """Every batch under runs_root, newest first, with counts but without the run list."""
    runs_root, stored = Path(runs_root), _stored_run_ids(conn)
    return [{k: v for k, v in _batch(p, runs_root, stored).items() if k != "runs"}
            for p in _batch_dirs(runs_root)]


def batch_detail(runs_root, batch_id, conn=None) -> dict | None:
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        return None
    path = Path(runs_root) / batch_id
    if not path.is_dir():
        return None
    return _batch(path, Path(runs_root), _stored_run_ids(conn))
