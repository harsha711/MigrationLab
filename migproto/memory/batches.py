"""Read-only view of Brev measurement batches (runs/brev-batch-*/) for the Batches page.

A batch directory is written by scripts/brev/supervise.py: `runs.jsonl` (one line per review),
plus hand-written notes. Each review's own run directory (runs/<run_id>/) holds the record:
`summary.json` and, when tracing was on, `agents/trace.json`. Nothing here writes files or
touches the evidence store.

Rules:
- The run directory's `summary.json` is the record; the `runs.jsonl` line is only used for
  fields the summary lacks (or when the run directory is missing, which is reported).
- `INFRA-NOTE` is free text. A run is an infrastructure failure when its run ID or its output
  stem (`NN-<scenario>-<topology>`, the supervisor's .out file) appears in it. Those runs are
  listed separately and never counted as model results.
- Verdicts other than PASS / FAIL / INCOMPLETE / ERROR are counted as `other`, not dropped.
- Every attempt is listed, failed ones included (AGENTS.md evidence rule 8).
"""

import json
import re
from pathlib import Path

from . import store

BATCH_NAME = re.compile(r"^brev-batch-[A-Za-z0-9_-]+$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")  # one path component, no traversal
VERDICTS = ("PASS", "FAIL", "INCOMPLETE", "ERROR")
NOTES = {"config_note": "CONFIG-NOTE", "infra_note": "INFRA-NOTE",
         "environment": "environment.txt"}


def _runs_root(runs_root) -> Path:
    return Path(runs_root) if runs_root else store.ROOT / "runs"


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError):
        return None


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _records(batch_dir: Path) -> tuple[list[dict], int]:
    """runs.jsonl lines, plus the number of lines that could not be parsed."""
    text = _text(batch_dir / "runs.jsonl") or ""
    records, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(rec, dict):
            records.append(rec)
        else:
            bad += 1
    return records, bad


def _stem(rec: dict) -> str | None:
    if rec.get("n") is None or not rec.get("topology"):
        return None
    try:
        return f"{int(rec['n']):02d}-{rec.get('scenario') or 'rename-note'}-{rec['topology']}"
    except (TypeError, ValueError):
        return None


def _is_infra(rec: dict, infra_note: str | None) -> bool:
    if not infra_note:
        return False
    keys = [k for k in (rec.get("run_id"), _stem(rec)) if k]
    return any(re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", infra_note) for k in keys)


def _trace(run_dir: Path) -> dict | None:
    t = _json(run_dir / "agents" / "trace.json")
    if not isinstance(t, dict):
        return None
    url = t.get("url")
    # Only http(s) links are rendered; anything else is kept as unknown.
    if not (isinstance(url, str) and re.match(r"^https?://", url)):
        url = None
    return {"url": url, "trace_id": t.get("trace_id"), "environment": t.get("environment")}


def _run(rec: dict, root: Path, infra_note: str | None) -> dict:
    run_id = rec.get("run_id")
    run_dir = root / run_id if isinstance(run_id, str) and RUN_ID.match(run_id) else None
    summary = _json(run_dir / "summary.json") if run_dir else None
    summary = summary if isinstance(summary, dict) else None
    totals = (summary or {}).get("agent_totals") or {}
    attempts = []
    for a in (summary or {}).get("attempts") or []:
        failed = a.get("failed_checks") or []
        attempts.append({"attempt": a.get("attempt"), "plan_id": a.get("plan_id"),
                         "ok": a.get("ok"), "halted": a.get("halted"),
                         "failed_checks": failed, "failed_count": len(failed)})
    pick = lambda key: totals.get(key) if totals.get(key) is not None else rec.get(key)
    return {
        "n": rec.get("n"),
        "stem": _stem(rec),
        "run_id": run_id,
        "run_dir_found": summary is not None,
        "scenario": (summary or {}).get("scenario") or rec.get("scenario") or "rename-note",
        "topology": (summary or {}).get("topology") or rec.get("topology"),
        "worker": rec.get("worker"),
        "verdict": (summary or {}).get("verdict") or rec.get("verdict"),
        "verdict_source": "summary.json" if summary and summary.get("verdict") else "runs.jsonl",
        "exit": rec.get("exit"),
        "model": (summary or {}).get("model"),
        "total_tokens": pick("total_tokens"),
        "model_calls": pick("model_calls"),
        "wall_seconds": pick("wall_seconds") if pick("wall_seconds") is not None
        else (summary or {}).get("elapsed_s", rec.get("seconds")),
        "exhausted_reason": pick("exhausted_reason"),
        "reason": (summary or {}).get("reason", rec.get("reason")),
        "spend_usd": rec.get("spend_usd"),
        "attempts": attempts,
        "trace": _trace(run_dir) if run_dir else None,
        "infra": _is_infra(rec, infra_note),
    }


def _counts(runs: list[dict]) -> list[dict]:
    """Per scenario and topology: PASS / FAIL / INCOMPLETE / ERROR / other."""
    table: dict = {}
    for r in runs:
        key = (r["scenario"], r["topology"] or "unknown")
        c = table.setdefault(key, {**{v: 0 for v in VERDICTS}, "other": 0, "runs": 0})
        c["runs"] += 1
        c[r["verdict"] if r["verdict"] in VERDICTS else "other"] += 1
    return [{"scenario": s, "topology": t, **c} for (s, t), c in sorted(table.items())]


def load_batch(batch_dir: Path, runs_root=None) -> dict:
    root = _runs_root(runs_root)
    notes = {k: _text(batch_dir / f) for k, f in NOTES.items()}
    records, bad = _records(batch_dir)
    runs = [_run(rec, root, notes["infra_note"]) for rec in records]
    model_runs = [r for r in runs if not r["infra"]]
    return {
        "name": batch_dir.name,
        **notes,
        "has_runs_jsonl": (batch_dir / "runs.jsonl").is_file(),
        "unparsed_lines": bad,
        "counts": _counts(model_runs),
        "runs": model_runs,
        "infra_runs": [r for r in runs if r["infra"]],
    }


def list_batches(runs_root=None) -> list[dict]:
    """Newest first. Per batch: name, notes, counts (model runs only) and run totals."""
    root = _runs_root(runs_root)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if d.is_dir() and BATCH_NAME.match(d.name):
            b = load_batch(d, root)
            out.append({"name": b["name"], "config_note": b["config_note"],
                        "infra_note": b["infra_note"], "counts": b["counts"],
                        "model_runs": len(b["runs"]), "infra_runs": len(b["infra_runs"]),
                        "unparsed_lines": b["unparsed_lines"]})
    return out


def batch_detail(name: str, runs_root=None) -> dict | None:
    if not isinstance(name, str) or not BATCH_NAME.match(name):
        raise ValueError("invalid batch name")
    d = _runs_root(runs_root) / name
    return load_batch(d, runs_root) if d.is_dir() else None
