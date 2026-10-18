"""Export the open dataset in dataset/: seed rows, scenario catalogue, recorded Lightning reviews.

Usage: uv run python scripts/export_dataset.py [--runs runs] [--out dataset]

Needs the local Postgres from `migproto setup` with the seed template (`migproto baseline`).
Reads run directories read-only. Writes:
  dataset/seed/account_user.jsonl      start-state rows of the table every scenario changes
  dataset/scenarios/scenarios.json     the 14 scenario definitions (migproto/scenarios.py)
  dataset/reviews/reviews.jsonl        one row per recorded Nemotron 3.5 Lightning review
  dataset/reviews/plans/<run_id>.json  the final plan of each review that saved one
  dataset/MANIFEST.json                counts and SHA-256 per file
No machine paths, hostnames or credentials are written.
"""

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import re
from pathlib import Path

from migproto import db, scenarios
from migproto.config import SEED_DB

ROOT = Path(__file__).resolve().parents[1]
MODEL = "nemotron-3.5-lightning"
SEED_COLUMNS = ("id", "email", "first_name", "last_name", "note", "is_staff", "is_active")


def _score_target():
    spec = importlib.util.spec_from_file_location("score", ROOT / "scripts/brev/score_scenarios.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.target


def export_seed(out: Path) -> int:
    cols = ", ".join(SEED_COLUMNS)
    with db.connect(SEED_DB) as conn:
        rows = conn.execute(f"SELECT {cols} FROM account_user ORDER BY id").fetchall()
    path = out / "seed" / "account_user.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(zip(SEED_COLUMNS, r)), ensure_ascii=False) + "\n")
    return len(rows)


def export_scenarios(out: Path) -> int:
    items = [dataclasses.asdict(s) for s in scenarios.SCENARIOS.values()]
    path = out / "scenarios" / "scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(items)


# Batches whose CONFIG-NOTE is missing or does not name the configuration; mapping from
# docs/results.md (configuration A settings, H second half, I, scenario sweeps 1-2).
BATCH_LABELS = {
    "brev-batch-20261017-184356": "A", "brev-batch-20261017-185514": "A",
    "brev-batch-20261017-191348": "A", "brev-batch-20261017-204405": "SWEEP-1",
    "brev-batch-20261017-212229": "SWEEP-2", "brev-batch-20261018-013233": "H",
    "brev-batch-20261018-082105": "I",
}


def _batches(runs: Path) -> dict[str, dict]:
    """run_id -> {batch_id, configuration, infrastructure} from supervisor batch records."""
    out = {}
    for b in sorted(runs.glob("brev-batch-*")):
        note = (b / "CONFIG-NOTE").read_text(encoding="utf-8") if (b / "CONFIG-NOTE").is_file() else ""
        m = re.search(r"\bconfiguration\s+([A-Z0-9_-]+)\b", note, re.IGNORECASE)
        infra = (b / "INFRA-NOTE").read_text(encoding="utf-8") if (b / "INFRA-NOTE").is_file() else ""
        if not (b / "runs.jsonl").is_file():
            continue
        for line in (b / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rid = rec.get("run_id")
            if rid:
                label = BATCH_LABELS.get(b.name) or (m.group(1).upper() if m else "SMOKE")
                out[rid] = {"batch_id": b.name, "configuration": label,
                            "_topology": rec.get("topology"), "_scenario": rec.get("scenario"),
                            "batch_index": rec.get("n"), "infrastructure_failure": rid in infra}
    return out


def export_reviews(runs: Path, out: Path) -> int:
    target = _score_target()
    batches = _batches(runs)
    plans_dir = out / "reviews" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with (out / "reviews" / "reviews.jsonl").open("w", encoding="utf-8") as f:
        for summary in sorted(runs.glob("*-agent-loop-llm/summary.json")):
            try:
                s = json.loads(summary.read_text(encoding="utf-8"))
            except ValueError:
                continue
            run_id = s.get("run_id") or summary.parent.name
            # Every Brev batch served Lightning; a review that failed before its first model
            # call (e.g. a port clash) has no model in summary.json but still counts.
            if s.get("model") != MODEL and run_id not in batches:
                continue
            batch = dict(batches.get(run_id, {"batch_id": None, "configuration": None,
                                              "batch_index": None, "infrastructure_failure": False}))
            topology = s.get("topology") or batch.get("_topology")
            scenario = s.get("scenario") or batch.get("_scenario") or "rename-note"
            batch.pop("_topology", None)
            batch.pop("_scenario", None)
            tgt = target(scenario)
            rec = s.get("runner_recommendation")
            matched = s.get("verdict") == tgt["verdict"] and rec in tgt["recommendations"]
            if scenario == "rename-note" and not rec:   # rename runs predate recommendations
                matched = s.get("verdict") == "PASS"
            attempts = s.get("attempts") or []
            final_plan = None
            if attempts and attempts[-1].get("plan_id"):
                p = summary.parent / f"plan-{attempts[-1]['plan_id']}.json"
                if p.is_file():
                    final_plan = f"plans/{run_id}.json"
                    (plans_dir / f"{run_id}.json").write_text(p.read_text(encoding="utf-8"),
                                                              encoding="utf-8")
            totals = s.get("agent_totals") or {}
            row = {
                "run_id": run_id, "scenario": scenario, "topology": topology,
                "model": s.get("model") or MODEL, **batch,
                "verdict": s.get("verdict"), "reason": s.get("reason"),
                "runner_recommendation": rec, "agent_recommendation": s.get("recommendation"),
                "expected": tgt, "matches_expected": matched,
                "attempts": [{"attempt": a.get("attempt"), "plan_id": a.get("plan_id"),
                              "ok": a.get("ok"), "halted": a.get("halted"),
                              "failed_checks": a.get("failed_checks") or []} for a in attempts],
                "checks_total": s.get("checks_total"), "checks_failed": s.get("checks_failed"),
                "budget": {k: totals.get(k) for k in ("max_revisions", "max_model_calls",
                                                       "max_total_tokens", "max_wall_seconds")},
                "usage": {k: totals.get(k) for k in ("model_calls", "prompt_tokens",
                                                      "completion_tokens", "total_tokens",
                                                      "model_seconds", "wall_seconds",
                                                      "exhausted_reason")},
                "final_plan": final_plan,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def manifest(out: Path, counts: dict) -> None:
    files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(out.rglob("*")) if p.is_file() and p.name not in {"MANIFEST.json",
                                                                              "README.md"}}
    (out / "MANIFEST.json").write_text(json.dumps({"counts": counts, "sha256": files}, indent=1)
                                       + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    ap.add_argument("--out", default=str(ROOT / "dataset"))
    args = ap.parse_args()
    out = Path(args.out)
    counts = {"seed_rows": export_seed(out), "scenarios": export_scenarios(out),
              "reviews": export_reviews(Path(args.runs), out)}
    manifest(out, counts)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
