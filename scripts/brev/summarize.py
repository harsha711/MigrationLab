"""Print docs/results.md rows for a Brev batch, read only from saved run directories.

Usage: uv run python scripts/brev/summarize.py runs/brev-batch-<ts> [more batch dirs]

One row per run, failures and INCOMPLETE runs included, in the order they ran. A footer gives
per-topology counts only; it is not a superiority claim (AGENTS.md).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def row(rec: dict) -> tuple[str, str]:
    run_dir = ROOT / "runs" / (rec.get("run_id") or "")
    if not rec.get("run_id") or not (run_dir / "summary.json").is_file():
        return rec["topology"], (f"| (no run dir) | {rec['topology']} | {rec.get('scenario', 'rename-note')} | - | - | - "
                                 f"| {rec.get('seconds')} s | - | {rec.get('verdict')} |")
    s = json.loads((run_dir / "summary.json").read_text())
    t = s.get("agent_totals") or {}
    calls = t.get("model_calls")
    latency = (f"{t['model_seconds'] / calls:.1f} s/call ({t['model_seconds']:.0f} s)"
               if calls and t.get("model_seconds") is not None else "-")
    attempts = ", ".join(
        f"a{a['attempt']} {'PASS' if a.get('ok') else 'FAIL'}"
        + (f" ({', '.join(a['failed_checks'][:3])}"
           + (f" +{len(a['failed_checks']) - 3}" if len(a['failed_checks']) > 3 else "") + ")"
           if a.get("failed_checks") else "")
        for a in s.get("attempts") or []) or "none"
    verdict = s.get("verdict")
    if t.get("exhausted_reason"):
        verdict += f" ({t['exhausted_reason']})"
    return s.get("topology") or rec["topology"], (
        f"| `{run_dir.name}` | {s.get('topology')} | {rec.get('scenario', 'rename-note')} "
        f"| {t.get('valid_proposals')} / {calls} | {t.get('total_tokens')} | {latency} "
        f"| {t.get('wall_seconds', s.get('elapsed_s'))} s | {attempts} | {verdict} |")


def main(dirs):
    recs = []
    for d in dirs:
        p = Path(d) / "runs.jsonl"
        recs += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    print("| Run | Topology | Scenario | Valid proposals / calls | Tokens | Model latency "
          "| Review time | Attempts | Runner verdict |")
    print("|---|---|---|---|---|---|---|---|---|")
    counts: dict = {}
    for rec in recs:
        topology, line = row(rec)
        print(line)
        counts.setdefault(topology, {}).setdefault(rec.get("verdict"), 0)
        counts[topology][rec.get("verdict")] += 1
    print()
    for topology, c in counts.items():
        print(f"- {topology}: {sum(c.values())} runs, "
              + ", ".join(f"{v} {k}" for k, v in sorted(c.items())))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
