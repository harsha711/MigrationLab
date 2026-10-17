"""Copy a small, sanitized subset of a saved run into examples/<name>/.

Usage:
    uv run python scripts/export_example.py runs/<run-dir> <fixture-name> --kind <kind>

Sanitization: absolute repository and home paths are replaced, model transcripts are
reduced to per-call metadata (latency, token usage, errors) without prompts, and server
logs are not copied. Outcomes and provenance fields are copied unchanged.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = str(Path.home())
CONTRACT_VERSION = "migproto.events/v0"

COPY_PATTERNS = [
    "summary.json", "events.jsonl", "evidence.json", "reproduce.sh", "plan-*.json",
    "sql/*.sql", "agents/investigator.json", "agents/planner-*.json",
    "agents/tester-propose-*.json", "agents/tester-report-*.json", "agents/search_hits.json",
]
FORBIDDEN = re.compile(r"(authorization|bearer\s+[A-Za-z0-9]|eyJ[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{10,})",
                       re.IGNORECASE)


def sanitize(text: str) -> str:
    text = text.replace(str(ROOT), "<repo>")
    return text.replace(HOME, "<home>")


def model_calls(run: Path) -> list[dict]:
    calls = []
    for path in sorted((run / "agents").glob("transcript-*.json")):
        role = path.stem.removeprefix("transcript-")
        for entry in json.loads(path.read_text()):
            calls.append({"role": role, "call_attempt": entry.get("attempt"),
                          "seconds": entry.get("seconds"), "usage": entry.get("usage"),
                          "error": entry.get("error")})
    return calls


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("name")
    p.add_argument("--kind", required=True)
    p.add_argument("--note", default="")
    args = p.parse_args()
    run = args.run.resolve()
    out = ROOT / "examples" / args.name
    if out.exists():
        shutil.rmtree(out)
    copied = []
    for pattern in COPY_PATTERNS:
        for src in sorted(run.glob(pattern)):
            text = sanitize(src.read_text())
            if FORBIDDEN.search(text):
                print(f"refusing {src}: looks like it contains a credential", file=sys.stderr)
                return 1
            dst = out / src.relative_to(run)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text)
            copied.append(str(src.relative_to(run)))
    calls = model_calls(run) if (run / "agents").exists() else []
    if calls:
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "agents" / "model_calls.json").write_text(json.dumps(calls, indent=2))
    summary = json.loads((run / "summary.json").read_text())
    manifest = {
        "fixture": args.name,
        "kind": args.kind,
        "contract_version": CONTRACT_VERSION,
        "source_run_id": run.name,
        "verdict": summary.get("verdict"),
        "checks_total": summary.get("checks_total"),
        "checks_failed": summary.get("checks_failed"),
        "files": copied + (["agents/model_calls.json (derived from transcripts)"] if calls else []),
        "sanitization": [
            "absolute repo path -> <repo>, home path -> <home>",
            "model transcripts reduced to role/latency/usage/error (prompts and responses dropped)",
            "server, migration and pytest logs not copied",
        ],
        "note": args.note,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out} ({len(copied)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
