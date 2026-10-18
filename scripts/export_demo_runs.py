"""Export the recorded Nemotron 3.5 Lightning runs and Brev batch records for the hosted UI.

Usage:
    uv run python scripts/export_demo_runs.py [--src runs] [--out demo/runs]

Writes demo/runs/ (git-ignored) and demo/runs.tar.xz (committed; render.yaml unpacks it at
build time). File modification times are kept, because the UI calls a batch without a DONE
line "running" until its supervisor.log has been quiet for 45 minutes.

Copies only what the review UI reads. Runs: summary, events, plans, SQL and the agents' JSON
outputs. Batches: supervisor.log, runs.jsonl, CONFIG-NOTE, INFRA-NOTE, STOP, request files.
Left out: model transcripts (full prompts), Langfuse trace links, server and migration logs,
per-run supervisor output, serving env files, environment dumps and vLLM logs. Only runs whose
provenance is agent:nemotron-3.5-lightning are copied, so local stand-in, scripted and
development-model runs never appear next to the Lightning results. Home and repository paths
are replaced; the export fails if anything that looks like a credential remains.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = "agent:nemotron-3.5-lightning"
RUN_FILES = ["summary.json", "events.jsonl", "request.json", "plan-*.json", "sql/*.sql",
             "agents/*.json"]
AGENT_EXCLUDE = re.compile(r"(transcript-.*|trace)\.json\Z")
BATCH_FILES = ["supervisor.log", "runs.jsonl", "CONFIG-NOTE", "INFRA-NOTE", "STOP",
               "request*.json"]
PATHS = re.compile(r"/home/[A-Za-z0-9_.-]+(/Documents/hackathon[A-Za-z0-9_-]*|/migrationlab)?")
FORBIDDEN = re.compile(r"(authorization|bearer\s+[A-Za-z0-9]|eyJ[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{10,}"
                       r"|sk-lf-|pk-lf-|BEGIN [A-Z ]*PRIVATE KEY|@gmail\.com)", re.IGNORECASE)


def sanitize(text: str) -> str:
    return PATHS.sub(lambda m: "<repo>" if m.group(1) else "<home>", text)


def copy(src: Path, dst: Path) -> None:
    text = sanitize(src.read_text(encoding="utf-8", errors="replace"))
    if FORBIDDEN.search(text):
        sys.exit(f"refusing to export {src}: matches a credential pattern")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    st = src.stat()
    os.utime(dst, (st.st_atime, st.st_mtime))


def lightning(run: Path) -> bool:
    try:
        return json.loads((run / "summary.json").read_text()).get("provenance") == PROVENANCE
    except (OSError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "runs"))
    ap.add_argument("--out", default=str(ROOT / "demo" / "runs"))
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    runs = batches = 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        if d.name.startswith("brev-batch-"):
            patterns = BATCH_FILES
            batches += 1
        elif lightning(d):
            patterns = RUN_FILES
            runs += 1
        else:
            continue
        for pattern in patterns:
            for f in sorted(d.glob(pattern)):
                if f.is_file() and not AGENT_EXCLUDE.search(f.name):
                    copy(f, out / d.name / f.relative_to(d))
    archive = out.with_suffix(".tar.xz")
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(out, arcname=out.name)
    print(f"exported {runs} Lightning runs and {batches} batches to {out} and {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
