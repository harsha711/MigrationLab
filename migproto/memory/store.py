"""Append-only SQLite store for scoped runner observations and agent claims.

Default DB: data/memory.sqlite (git-ignored by *.sqlite). Ingesting a run twice is a
no-op (primary keys + INSERT OR IGNORE): history is never overwritten. Unknown scope is
stored as NULL. Observations (runner) and claims (agents) live in separate tables.
"""

import json
import sqlite3
from pathlib import Path

from . import adapter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "memory.sqlite"
SOURCES = (ROOT / "examples", ROOT / "runs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  path TEXT, source_kind TEXT,           -- 'fixture' | 'local_run'
  command TEXT, verdict TEXT, elapsed_s REAL,
  checks_total INTEGER, provenance TEXT, model TEXT,
  meta_json TEXT, summary_json TEXT, manifest_json TEXT, model_calls_json TEXT,
  request_json TEXT, topology TEXT,      -- Owner 2 review request / agent topology
  ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY,           -- '<run_id>/a<N>'
  run_id TEXT REFERENCES runs, attempt TEXT, plan_id TEXT,
  status TEXT, ok INTEGER, halted TEXT, failed_json TEXT
);
CREATE TABLE IF NOT EXISTS plans (
  run_id TEXT REFERENCES runs, file TEXT, plan_id TEXT, attempt_id TEXT,
  body_json TEXT, sql TEXT,
  parent_plan_id TEXT, solution_origin TEXT,   -- from planner `proposal` events
  PRIMARY KEY (run_id, file)
);
CREATE TABLE IF NOT EXISTS stages (
  run_id TEXT REFERENCES runs, seq INTEGER, attempt_id TEXT, stage_id TEXT,
  stage_kind TEXT, phase TEXT, description TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs, attempt_id TEXT, plan_id TEXT, stage_id TEXT,
  check_name TEXT,
  status TEXT,                           -- as recorded (v0: PASS / FAIL)
  outcome TEXT,                          -- v1 pass/fail/refused/error/skipped; NULL in v0
  expectation TEXT,
  check_type TEXT,                       -- v1 assertion / safety_gate / execution
  app_scope TEXT,                        -- v1 app_version: old / new / both / database
  app_commit_json TEXT, database_snapshot_id TEXT,
  expected_json TEXT, observed_json TEXT,
  schema_state_json TEXT,                -- NULL = unknown
  definition_provenance TEXT, executed_by TEXT, scope_source TEXT,
  seq INTEGER, error TEXT, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS observation_steps (
  observation_id TEXT REFERENCES observations, step INTEGER,
  app_version TEXT, op TEXT, user_label TEXT, ok INTEGER, value TEXT, error TEXT,
  PRIMARY KEY (observation_id, step)
);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs, attempt_id TEXT, role TEXT, kind TEXT,
  text TEXT, provenance TEXT, structured INTEGER
);
CREATE TABLE IF NOT EXISTS claim_evidence (
  claim_id TEXT REFERENCES claims, observation_id TEXT,
  resolved INTEGER,                      -- 1 if the ID matches an observation of the run
  PRIMARY KEY (claim_id, observation_id)
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT REFERENCES runs, seq INTEGER, attempt_id TEXT, stage_id TEXT,
  kind TEXT, t REAL, raw_json TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS obs_scope ON observations (run_id, attempt_id, stage_id, check_name);
CREATE INDEX IF NOT EXISTS steps_app ON observation_steps (app_version);
"""


# Columns added after the first schema. Older stores get them via ALTER TABLE (additive,
# existing rows kept, new columns NULL = unknown for runs ingested before the upgrade).
ADDED_COLUMNS = {
    "observations": {"check_type": "TEXT", "app_scope": "TEXT", "app_commit_json": "TEXT",
                     "database_snapshot_id": "TEXT", "expected_json": "TEXT",
                     "observed_json": "TEXT"},
    "runs": {"request_json": "TEXT", "topology": "TEXT"},
    "plans": {"parent_plan_id": "TEXT", "solution_origin": "TEXT"},
    "claim_evidence": {"resolved": "INTEGER"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, sql_type in columns.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")
    conn.commit()


def connect(path=None) -> sqlite3.Connection:
    """Open (and initialize) the store. `":memory:"` works for tests."""
    path = DEFAULT_DB if path is None else path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _j(value):
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _aid(run_id, attempt):
    return f"{run_id}/{attempt}" if attempt else None


def ingest(conn: sqlite3.Connection, run_dir) -> str:
    """Normalize one run directory and append it. Returns its run_id."""
    r = adapter.load(run_dir)
    rid, meta = r["run_id"], r["meta"]
    ins = lambda table, row: conn.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()))
    with conn:
        ins("runs", {"run_id": rid, "path": r["path"], "source_kind": r["source_kind"],
                     "command": meta["command"], "verdict": meta["verdict"],
                     "elapsed_s": meta["elapsed_s"], "checks_total": meta["checks_total"],
                     "provenance": meta["provenance"], "model": meta["model"],
                     "meta_json": _j(meta), "summary_json": _j(r["summary"]),
                     "manifest_json": _j(r["manifest"]), "model_calls_json": _j(r["model_calls"]),
                     "request_json": _j(meta["request"]), "topology": meta["topology"]})
        for a in r["attempts"]:
            ok = a.get("ok")
            ins("attempts", {"attempt_id": _aid(rid, a["attempt"]), "run_id": rid,
                             "attempt": a["attempt"], "plan_id": a.get("plan_id"),
                             "status": a.get("status"), "ok": None if ok is None else int(ok),
                             "halted": a.get("halted"), "failed_json": _j(a.get("failed"))})
        for p in r["plans"]:
            ins("plans", {"run_id": rid, "file": p["file"], "plan_id": p["plan_id"],
                          "attempt_id": _aid(rid, p["attempt"]), "body_json": _j(p["body"]),
                          "sql": p["sql"], "parent_plan_id": p.get("parent_plan_id"),
                          "solution_origin": p.get("solution_origin")})
        for s in r["stages"]:
            ins("stages", {"run_id": rid, "seq": s["seq"], "attempt_id": _aid(rid, s["attempt"]),
                           "stage_id": s["stage_id"], "stage_kind": s["stage_kind"],
                           "phase": s["phase"], "description": s["description"]})
        for o in r["observations"]:
            ins("observations", {
                "observation_id": o["observation_id"], "run_id": o["run_id"],
                "attempt_id": _aid(o["run_id"], o["attempt"]), "plan_id": o["plan_id"],
                "stage_id": o["stage_id"], "check_name": o["check_name"], "status": o["status"],
                "outcome": o["outcome"], "expectation": o.get("expectation"),
                "check_type": o["check_type"], "app_scope": o["app_scope"],
                "app_commit_json": _j(o["app_commit"]),
                "database_snapshot_id": o["database_snapshot_id"],
                "expected_json": _j(o["expected"]), "observed_json": _j(o["observed"]),
                "schema_state_json": _j(o["schema_state"]),
                "definition_provenance": o["definition_provenance"],
                "executed_by": o["executed_by"], "scope_source": o["scope_source"],
                "seq": o["seq"],
                "error": o["error"] if o["error"] is None or isinstance(o["error"], str)
                else _j(o["error"]),
                "raw_json": _j(o["raw"])})
            for s in o["steps"]:
                ok = s["ok"]
                ins("observation_steps", {
                    "observation_id": o["observation_id"], "step": s["step"],
                    "app_version": s["app_version"], "op": s["op"], "user_label": s["user"],
                    "ok": None if ok is None else int(ok),
                    "value": None if s["value"] is None else str(s["value"]), "error": s["error"]})
        for c in r["claims"]:
            ins("claims", {"claim_id": c["claim_id"], "run_id": rid,
                           "attempt_id": _aid(rid, c["attempt"]), "role": c["role"],
                           "kind": c["kind"], "text": c["text"], "provenance": c["provenance"],
                           "structured": c["structured"]})
            for link in c["evidence"]:
                ins("claim_evidence", {"claim_id": c["claim_id"], **link})
        for e in r["events"]:
            ins("events", {"run_id": rid, "seq": e["seq"], "attempt_id": _aid(rid, e["attempt"]),
                           "stage_id": e["stage_id"], "kind": e["kind"], "t": e["t"],
                           "raw_json": _j(e["raw"])})
    return rid


def run_dirs(sources=SOURCES):
    """Finished runs under examples/ and runs/: an events.jsonl and a summary.json.

    A run without summary.json is still in progress (or crashed before writing it).
    Ingesting it would freeze a partial run into the append-only store, so it is skipped.
    """
    for base in sources:
        if Path(base).is_dir():
            yield from sorted(p.parent for p in Path(base).glob("*/events.jsonl")
                              if (p.parent / "summary.json").is_file())


def ingest_all(conn: sqlite3.Connection, sources=SOURCES) -> list[str]:
    return [ingest(conn, d) for d in run_dirs(sources)]
