"""Score agent scenario reviews against the manual expected verdicts (evaluation side only).

Usage: uv run python scripts/brev/score_scenarios.py runs/brev-batch-<ts> [more batch dirs]

Reads proposals/manual/scenarios/<id>.json, which agents never see. For each run it compares:
- the runner's verdict and recommendation for the agent's final plan, against
- the set of (verdict, recommendation) outcomes the manual file lists as acceptable for that
  scenario: a staged/accepted PASS when a safe plan exists, or reject when every listed plan
  is rejected (no safe pattern).
An unsafe scripted plan's FAIL/reject is not the agent's target; the target is the best outcome
listed. Runs named in a batch INFRA-NOTE are reported as infrastructure, not scored.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "proposals" / "manual" / "scenarios"


def target(scenario: str) -> dict:
    plans = json.loads((EXPECTED / f"{scenario}.json").read_text())["plans"]
    passing = sorted({v["recommendation"] for v in plans.values() if v["verdict"] == "PASS"})
    if passing:
        return {"verdict": "PASS", "recommendations": passing}
    return {"verdict": "FAIL", "recommendations": ["reject"]}


def main(dirs):
    rows, infra = [], set()
    for d in map(Path, dirs):
        note = d / "INFRA-NOTE"
        if note.is_file():
            infra |= {w for w in note.read_text().split() if w.startswith("2026")}
        rows += [json.loads(l) for l in (d / "runs.jsonl").read_text().splitlines() if l.strip()]
    print("| Scenario | Topology | Run | Runner verdict | Runner rec. | Agent rec. | Attempts "
          "(failed checks) | Target | Match |")
    print("|---|---|---|---|---|---|---|---|---|")
    matched = scored = 0
    for r in rows:
        sc = r.get("scenario", "rename-note")
        run_id = r.get("run_id") or ""
        if any(run_id.startswith(i) for i in infra):
            print(f"| {sc} | {r['topology']} | `{run_id}` | infrastructure failure | | | | | n/a |")
            continue
        s = json.loads((ROOT / "runs" / run_id / "summary.json").read_text()) if run_id else {}
        tgt = target(sc)
        rec = s.get("runner_recommendation")
        ok = s.get("verdict") == tgt["verdict"] and rec in tgt["recommendations"]
        if sc == "rename-note" and not rec:  # rename runs predate recommendations
            ok = s.get("verdict") == "PASS"
        scored += 1
        matched += ok
        attempts = " → ".join(str(len(a["failed_checks"])) for a in s.get("attempts", []))
        print(f"| {sc} | {r['topology']} | `{run_id}` | {s.get('verdict')} | {rec} | "
              f"{s.get('recommendation')} | {attempts} | {tgt['verdict']} "
              f"{'/'.join(tgt['recommendations'])} | {'yes' if ok else 'no'} |")
    print(f"\nMatched {matched} of {scored} scored runs.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
