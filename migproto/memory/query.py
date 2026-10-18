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
import statistics
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

APP_ORDER = ["old", "new-staged", "new-unsafe"]
BATCH_PREFIX = "brev-batch-"
BATCH_VERDICTS = ("PASS", "FAIL", "INCOMPLETE", "ERROR")
# A batch without DONE whose supervisor.log has not changed for this long is "stopped"
# (cancelled or killed), not running. Reviews log a line at least every ~20 min.
STALE_LOG_SECONDS = 45 * 60
# Answer labels for scoring finished batches in the UI. Evaluation side only: nothing an agent
# calls (memory.retrieval, the controller) may use expected_target or this path.
EXPECTED_VERDICTS = Path(__file__).resolve().parents[2] / "proposals" / "manual" / "scenarios"
_BATCH_ID = re.compile(r"brev-batch-[\w.-]+")
_RUN_ID = re.compile(r"\w[\w.-]*")  # one path component, never "." or ".."
_SCENARIO_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
# Run stamps in INFRA-NOTE: "20261017-192959" or, after a full one, the short form "-193746".
_INFRA_STAMP = re.compile(r"(?<!\d)(\d{8})?-(\d{6})(?!\d)")
# supervisor.log: "21:04:16 RUN   9/10 <scenario> <topology> starting on worker 4 (~$12.82)".
# Older batches log "RUN   1/2 roles starting (~$2.07)" (no scenario, no worker).
_LOG_LINE = re.compile(r"(\d\d):(\d\d):(\d\d) +([A-Z]+)\b(.*)")
_LOG_START = re.compile(r"\s+(\d+)/(\d+) +(.+?) +starting(?: on worker (\d+))?\b")


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
    # Keep the library route lightweight. The UI used to request full run_detail() for every
    # row just to display these two counts, which meant 100+ HTTP requests for a real batch.
    rows = conn.execute(
        "SELECT r.run_id, r.source_kind, r.command, r.verdict, r.elapsed_s, r.checks_total, "
        "r.provenance, r.model, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.run_id) AS attempt_count, "
        "(SELECT COUNT(*) FROM observations o WHERE o.run_id = r.run_id "
        " AND o.status = 'FAIL') AS checks_failed "
        "FROM runs r ORDER BY r.source_kind, r.run_id")
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
# The only answer labels read are EXPECTED_VERDICTS, by expected_target, to score finished runs.
# Scripted plans are never read.

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


def _failed_trajectory(summary: dict, rec: dict) -> list[int] | None:
    """Failed checks for every recorded attempt, never just the final attempt."""
    attempts = summary.get("attempts")
    if isinstance(attempts, list):
        values = []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            failed = attempt.get("failed_checks")
            if isinstance(failed, list):
                values.append(len(failed))
            elif isinstance(failed, int):
                values.append(failed)
            elif isinstance(attempt.get("checks_failed"), int):
                values.append(attempt["checks_failed"])
        if values or attempts:
            return values
    outcomes = _attempt_outcomes(summary)
    if outcomes is not None:
        return [int(a.get("fail") or 0) for a in outcomes]
    failed = rec.get("checks_failed")
    return [failed] if isinstance(failed, int) else None


def _first(*values):
    return next((v for v in values if v is not None), None)


def expected_target(scenario, expected_root=EXPECTED_VERDICTS) -> dict | None:
    """The best outcome the manual file lists for a scenario; None when there is no file.

    Same rule as target() in scripts/brev/score_scenarios.py (not on this branch yet;
    deduplicate once it is): PASS with any listed PASS recommendation when a safe plan exists,
    else FAIL/reject.
    """
    if not isinstance(scenario, str) or not _SCENARIO_ID.fullmatch(scenario):
        return None
    data = _read_json(Path(expected_root) / f"{scenario}.json")
    plans = data.get("plans") if isinstance(data, dict) else None
    if not isinstance(plans, dict) or not plans:
        return None
    passing = sorted({p["recommendation"] for p in plans.values() if isinstance(p, dict)
                      and p.get("verdict") == "PASS" and isinstance(p.get("recommendation"), str)})
    if passing:
        return {"verdict": "PASS", "recommendations": passing}
    return {"verdict": "FAIL", "recommendations": ["reject"]}


def _matches(target: dict | None, summary: dict):
    """score_scenarios.py's match: the runner's verdict and recommendation for the final plan."""
    if target is None:
        return None
    return (summary.get("verdict") == target["verdict"]
            and summary.get("runner_recommendation") in target["recommendations"])


def supervisor_progress(path: Path, batch_date: str | None = None) -> dict | None:
    """Runs the supervisor started and whether it logged DONE; None without supervisor.log.

    Log times are HH:MM:SS (local time of the supervisor). The date comes from the batch name
    and advances by one day when the time goes back by more than 12 hours (midnight).
    """
    text = _read_text(Path(path) / "supervisor.log")
    if text is None:
        return None
    try:
        day = datetime.strptime(batch_date, "%Y%m%d") if batch_date else None
    except ValueError:
        day = None
    started, done, planned, last, prev = {}, False, None, None, None
    for line in text.splitlines():
        m = _LOG_LINE.match(line)
        if not m:
            continue
        t = timedelta(hours=int(m.group(1)), minutes=int(m.group(2)), seconds=int(m.group(3)))
        if prev is not None and prev - t > timedelta(hours=12) and day is not None:
            day += timedelta(days=1)
        prev = t
        at = day + t if day is not None else None
        last = at
        if m.group(4) == "DONE":
            done = True
        elif m.group(4) == "RUN" and (s := _LOG_START.match(m.group(5))):
            words = s.group(3).split()
            started[int(s.group(1))] = {
                "n": int(s.group(1)), "scenario": " ".join(words[:-1]) or None,
                "topology": words[-1], "worker": int(s.group(4)) if s.group(4) else None,
                "started_at": at}
            planned = int(s.group(2))
    return {"finished": done, "started": started, "planned": planned, "last_line_at": last}


def _log_age(path: Path, now: datetime) -> int | None:
    """Seconds since supervisor.log was last written, on the local clock (mtime vs now)."""
    try:
        return max(0, round(now.timestamp() - (path / "supervisor.log").stat().st_mtime))
    except OSError:
        return None


def _iso(at):
    return at.isoformat(timespec="seconds") if at else None


def _batch_run(rec: dict, runs_root: Path, stamps: set[str], stored: set[str],
               target_of, shared: list[dict]) -> dict:
    run_id = rec.get("run_id") if isinstance(rec.get("run_id"), str) else None
    run_dir = runs_root / run_id if run_id and _RUN_ID.fullmatch(run_id) else None
    summary = _read_json(run_dir / "summary.json") if run_dir else None
    s = summary if isinstance(summary, dict) else {}
    # Two runs started in the same second share a run directory; the later summary.json
    # replaces the earlier one. A summary that contradicts the supervisor's row is not this run's.
    conflict = [k for k in ("scenario", "topology") if rec.get(k) and s.get(k) and rec[k] != s[k]]
    if rec.get("verdict") in BATCH_VERDICTS and s.get("verdict") not in (None, rec["verdict"]):
        conflict.append("verdict")
    if conflict:
        s = {}
    totals = s.get("agent_totals") or {}
    trace = _read_json(run_dir / "agents" / "trace.json") if run_dir and not conflict else None
    url = trace.get("url") if isinstance(trace, dict) else None
    if not (isinstance(url, str) and url.startswith(("https://", "http://"))):
        url = None
    scenario = _first(rec.get("scenario"), s.get("scenario"))
    infra = _is_infra(run_id, stamps)
    target = None if infra else target_of(scenario)
    return {
        "status": "finished",
        "n": rec.get("n"),
        "run_id": run_id,
        "scenario": scenario,
        "topology": _first(s.get("topology"), rec.get("topology")),
        "verdict": _first(s.get("verdict"), rec.get("verdict")),
        "verdict_source": "summary.json" if s.get("verdict") else "runs.jsonl",
        "recommendation": s.get("recommendation"),
        "recommendation_recorded": "recommendation" in s,  # absent (older runs) = unknown
        "runner_recommendation": s.get("runner_recommendation"),
        "recommendation_agrees": s.get("recommendation_agrees"),
        "attempts": _attempt_outcomes(s),
        "failed_checks": _failed_trajectory(s, rec),
        "total_tokens": _first(totals.get("total_tokens"), rec.get("total_tokens")),
        "model_calls": _first(totals.get("model_calls"), rec.get("model_calls")),
        "wall_seconds": _first(totals.get("wall_seconds"), s.get("elapsed_s"), rec.get("seconds")),
        "stopped": _first(totals.get("exhausted_reason"), s.get("reason"), rec.get("reason")),
        "model": s.get("model"),
        "has_summary": bool(s),
        "in_store": run_id in stored,
        "trace_url": url,
        "infrastructure_failure": infra,
        "summary_conflict": conflict,
        "shared_with": shared,
        "expected": target,
        "matches_expected": None if conflict or shared else _matches(target, s),
    }


def _stored_run_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT run_id FROM runs")} if conn is not None else set()


def _in_progress(progress: dict | None, runs: list[dict], now: datetime,
                 stopped: bool = False, age: int | None = None) -> list[dict]:
    """Runs the log says started but runs.jsonl has no row for.

    "running" while the batch has no DONE line and is not stopped; otherwise "no_result"
    (the supervisor ended without recording them).
    """
    if not progress:
        return []
    recorded = {r["n"] for r in runs if isinstance(r["n"], int)}
    ended = progress["finished"] or stopped
    # A remote supervisor logs in its own clock (Brev: UTC). When the log is ahead of the
    # local clock, measure elapsed time on the log clock: last line + file age.
    last = progress["last_line_at"]
    if last is not None and age is not None and last > now + timedelta(minutes=1):
        now = last + timedelta(seconds=age)
    out = []
    for n, s in sorted(progress["started"].items()):
        if n in recorded:
            continue
        at = s["started_at"]
        out.append({**s, "started_at": _iso(at),
                    "status": "no_result" if ended else "running",
                    "elapsed_seconds": None if ended or at is None
                    else max(0, round((now - at).total_seconds()))})
    return out


def _run_id_rows(runs_root: Path) -> dict[str, list[dict]]:
    """Every runs.jsonl row naming each run ID, across all batches under runs_root."""
    rows = {}
    for p in _batch_dirs(runs_root):
        for rec in _read_jsonl(p / "runs.jsonl")[0]:
            if isinstance(rec.get("run_id"), str):
                rows.setdefault(rec["run_id"], []).append({"batch_id": p.name, "n": rec.get("n")})
    return rows


def _batch(path: Path, runs_root: Path, stored: set[str], now: datetime,
           expected_root, run_rows: dict[str, list[dict]]) -> dict:
    batch_id = path.name
    infra_note = _read_text(path / "INFRA-NOTE")
    m = re.match(r"brev-batch-(\d{8})-", batch_id)
    date = m.group(1) if m else None
    stamps = infra_run_stamps(infra_note, date)
    targets = {}

    def target_of(scenario):
        if scenario not in targets:
            targets[scenario] = expected_target(scenario, expected_root)
        return targets[scenario]

    def shared(rec):
        me = {"batch_id": batch_id, "n": rec.get("n")}
        return [o for o in run_rows.get(rec.get("run_id"), []) if o != me]

    records, bad = _read_jsonl(path / "runs.jsonl")
    runs = sorted((_batch_run(r, runs_root, stamps, stored, target_of,
                              shared(r) if isinstance(r.get("run_id"), str) else [])
                   for r in records),
                  key=lambda r: (r["n"] if isinstance(r["n"], int) else 10 ** 6, r["run_id"] or ""))
    progress = supervisor_progress(path, date)
    age = _log_age(path, now) if progress else None
    stopped = bool(progress and not progress["finished"] and (age or 0) > STALE_LOG_SECONDS)
    in_progress = _in_progress(progress, runs, now, stopped, age)
    cells = {}
    for r in runs:
        if r["infrastructure_failure"]:
            continue
        cell = cells.setdefault((r["scenario"], r["topology"]), {
            "scenario": r["scenario"], "topology": r["topology"],
            **{v: 0 for v in BATCH_VERDICTS}, "other": {}, "matched": 0, "scored": 0})
        verdict = str(r["verdict"]).upper() if r["verdict"] else "not recorded"
        if verdict in BATCH_VERDICTS:
            cell[verdict] += 1
        else:
            cell["other"][verdict] = cell["other"].get(verdict, 0) + 1
        if r["matches_expected"] is not None:
            cell["scored"] += 1
            cell["matched"] += r["matches_expected"]
    counts = sorted(cells.values(), key=lambda c: (c["scenario"] is None, c["scenario"] or "",
                                                   c["topology"] or ""))
    return {
        "batch_id": batch_id,
        # "finished" only on a DONE line; "stopped" when supervisor.log went quiet without
        # DONE (cancelled or killed); "unknown" without supervisor.log.
        "status": ("unknown" if progress is None
                   else "finished" if progress["finished"]
                   else "stopped" if stopped else "running"),
        "log_age_seconds": age,
        "planned_runs": progress["planned"] if progress else None,
        "log_last_line_at": _iso(progress["last_line_at"]) if progress else None,
        "checked_at": _iso(now),
        "in_progress": in_progress,
        "running_count": sum(r["status"] == "running" for r in in_progress),
        "scenario_score": {"matched": sum(c["matched"] for c in counts),
                           "scored": sum(c["scored"] for c in counts)},
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


def list_batches(runs_root, conn=None, *, now=None,
                 expected_root=EXPECTED_VERDICTS) -> list[dict]:
    """Every batch under runs_root, newest first, with counts but without the run list."""
    runs_root, stored, now = Path(runs_root), _stored_run_ids(conn), now or datetime.now()
    run_rows = _run_id_rows(runs_root)
    return [{k: v for k, v in _batch(p, runs_root, stored, now, expected_root, run_rows).items()
             if k != "runs"}
            for p in _batch_dirs(runs_root)]


def batch_detail(runs_root, batch_id, conn=None, *, now=None,
                 expected_root=EXPECTED_VERDICTS) -> dict | None:
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        return None
    path = Path(runs_root) / batch_id
    if not path.is_dir():
        return None
    return _batch(path, Path(runs_root), _stored_run_ids(conn), now or datetime.now(),
                  expected_root, _run_id_rows(Path(runs_root)))


# ---- Roles versus single: compact evidence-only demo view ----

_CONFIGURATION = re.compile(r"\bCONFIGURATION\s+([BCDEFGM])\b", re.IGNORECASE)


def _configuration_letter(note: str | None) -> str | None:
    match = _CONFIGURATION.search(note or "")
    return match.group(1).upper() if match else None


def _median(rows: list[dict], field: str):
    values = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
    return statistics.median(values) if values else None


def topology_comparison(runs_root, conn=None) -> dict:
    """Rename-only roles/single observations, grouped strictly by configuration.

    Each configuration has its own settings. This deliberately returns no pooled rate or
    cross-configuration winner. It reads the same immutable batch records as Batches.
    """
    root = Path(runs_root)
    stored = _stored_run_ids(conn)
    rows_by_id = _run_id_rows(root)
    grouped: dict[str, list[dict]] = {}
    notes = {}
    for path in _batch_dirs(root):
        note = _read_text(path / "CONFIG-NOTE")
        config = _configuration_letter(note)
        if config is None:
            continue
        batch = _batch(path, root, stored, datetime.now(), EXPECTED_VERDICTS, rows_by_id)
        notes.setdefault(config, note)
        grouped.setdefault(config, []).extend(
            row for row in batch["runs"]
            if not row["infrastructure_failure"]
            and row["scenario"] == "rename-note"
            and row["topology"] in {"roles", "single"}
        )

    configurations = []
    for config in sorted(grouped):
        reviews = grouped[config]
        setups = {}
        for topology in ("roles", "single"):
            rows = [r for r in reviews if r["topology"] == topology]
            counts = {verdict: 0 for verdict in BATCH_VERDICTS}
            stops = {"token_budget": 0, "call_budget": 0, "other": 0}
            for row in rows:
                verdict = str(row.get("verdict") or "").upper()
                if verdict in counts:
                    counts[verdict] += 1
                if verdict == "INCOMPLETE":
                    stopped = str(row.get("stopped") or "").lower()
                    stops["token_budget" if "token" in stopped else "call_budget" if "call" in stopped else "other"] += 1
            setups[topology] = {"reviews": len(rows), "verdicts": counts, "incomplete_stops": stops,
                                "median_tokens": _median(rows, "total_tokens"),
                                "median_seconds": _median(rows, "wall_seconds"), "runs": rows}
        configurations.append({"configuration": config, "config_note": notes.get(config), "setups": setups})
    return {"configurations": configurations}


def _scenario_label(scenario_id: str) -> str:
    """A useful label even when this checkout does not know a newer scenario ID."""
    return scenario_id.replace("-", " ").title()


def scenario_activity_overview(conn) -> dict:
    """Recorded reviews grouped by migration activity, never pooled across activities.

    Scenario metadata is descriptive UI context. Verdicts and measurements remain derived from
    each run's saved summary; a scenario introduced by another worktree gets a safe fallback.
    """
    try:
        from ..scenarios import SCENARIOS
    except ImportError:  # pragma: no cover - defensive for partial UI-only checkouts
        SCENARIOS = {}
    groups: dict[str, list[dict]] = {}
    rows = conn.execute("SELECT run_id, source_kind, verdict, elapsed_s, model, topology, "
                        "summary_json, meta_json, model_calls_json FROM runs ORDER BY run_id")
    for row in rows:
        summary = _obj(row["summary_json"]) or {}
        scenario = summary.get("scenario")
        if not isinstance(scenario, str) or not _SCENARIO_ID.fullmatch(scenario):
            continue
        totals = summary.get("agent_totals") or {}
        meta = _obj(row["meta_json"]) or {}
        source_tags = ((meta.get("request") or {}).get("source_tags") or [])
        if "human-assisted" in source_tags:
            # A human-assisted revision is useful on its own job page, but is not an
            # independent topology/model measurement for activity comparisons.
            continue
        calls = _obj(row["model_calls_json"]) or []
        groups.setdefault(scenario, []).append({
            "run_id": row["run_id"], "source_kind": row["source_kind"],
            "verdict": summary.get("verdict") or row["verdict"],
            "topology": summary.get("topology") or meta.get("topology") or row["topology"],
            "model": summary.get("model") or meta.get("model") or row["model"],
            "total_tokens": totals.get("total_tokens"),
            "wall_seconds": totals.get("wall_seconds", summary.get("elapsed_s", row["elapsed_s"])),
            "model_calls": totals.get("model_calls", len(calls) if calls else None),
        })

    activities = []
    for scenario_id, activity_runs in groups.items():
        definition = SCENARIOS.get(scenario_id)
        verdicts = {v: 0 for v in BATCH_VERDICTS}
        for run in activity_runs:
            verdict = str(run.get("verdict") or "").upper()
            if verdict in verdicts:
                verdicts[verdict] += 1
        groups_by_model: dict[tuple[str, str], list[dict]] = {}
        for run in activity_runs:
            key = (run["topology"] or "not recorded", run["model"] or "not recorded")
            groups_by_model.setdefault(key, []).append(run)
        performance = []
        for (topology, model), subset in sorted(groups_by_model.items()):
            counts = {v: sum(str(r.get("verdict") or "").upper() == v for r in subset)
                      for v in BATCH_VERDICTS}
            performance.append({"topology": topology, "model": model, "reviews": len(subset),
                                "verdicts": counts, "median_tokens": _median(subset, "total_tokens"),
                                "median_seconds": _median(subset, "wall_seconds"),
                                "median_calls": _median(subset, "model_calls")})
        change = definition.change if definition else {}
        activities.append({
            "scenario": scenario_id,
            "title": definition.title if definition else _scenario_label(scenario_id),
            "description": change.get("description") if definition else "Scenario definition is not available in this checkout.",
            "tags": list(definition.tags) if definition else [], "table": change.get("table"),
            "runs": activity_runs, "reviews": len(activity_runs), "verdicts": verdicts,
            "performance": performance,
        })
    return {"activities": sorted(activities, key=lambda activity: activity["scenario"])}
