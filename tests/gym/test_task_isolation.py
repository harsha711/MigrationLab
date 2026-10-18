"""Frozen Gym tasks must not contain answers (AGENTS.md: never expose proposals/scripted/**,
proposals/manual/**, patches/, examples/ outcomes or expected verdicts to evaluated agents).

Same answer corpus as tests/agents/test_prompt_isolation.py, applied to every string in
gym/tasks/migration_review.jsonl, plus the scenario titles and tags, which the controller does
not show either ("... with duplicates", "safe-control").
"""

import hashlib
import json

import pytest

from migproto import scenarios
from migproto.config import PROPOSALS, WORKTREES
from migration_review import tasks

FORBIDDEN_PATHS = ["proposals/scripted", "proposals/manual", "patches/", "examples/",
                   "heldout/", "ground_truth", '"verdict"', "expected verdict"]
ALLOWED_KEYS = {"task_id", "scenario_id", "task_version", "prompt_sha256",
                "responses_create_params"}


def _scripted_answers() -> set[str]:
    out = set()
    for path in (PROPOSALS / "scripted").rglob("*.json"):
        obj = json.loads(path.read_text())
        if not isinstance(obj, dict):
            continue
        for st in obj.get("stages", []):
            out.update(st.get("sql", []))
            if len(st.get("description", "")) > 30:
                out.add(st["description"])
        for key in ("summary", "write_consistency", "revision_notes", "rationale"):
            if len(str(obj.get(key) or "")) > 30:
                out.add(obj[key])
        for case in obj.get("custom_cases", []):
            out.add(case["id"])
            out.update(s["value"] for s in case["steps"] if s.get("value"))
        for finding in obj.get("findings", []):
            if len(finding.get("reason", "")) > 30:
                out.add(finding["reason"])
    return out


def _verdict_labels() -> set[str]:
    out = set()
    for path in (PROPOSALS / "manual" / "scenarios").glob("*.json"):
        label = json.loads(path.read_text())
        out.add(label["note"])
        for name, expected in label["plans"].items():
            out.add(expected["why"])
            out.add(f'"verdict": "{expected["verdict"]}"')
            out.add(f"{label['scenario']}/{name}")
    return out


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


ROWS = tasks.load()


def test_one_frozen_task_per_scenario():
    assert sorted(r["scenario_id"] for r in ROWS) == sorted(scenarios.SCENARIOS)
    assert len({r["task_id"] for r in ROWS}) == len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["scenario_id"])
def test_task_contains_no_answers(row):
    assert set(row) == ALLOWED_KEYS
    sc = scenarios.get(row["scenario_id"])
    text = "\n".join(_strings(row))
    for needle in FORBIDDEN_PATHS:
        assert needle not in text, needle
    for answer in _scripted_answers() | _verdict_labels():
        assert answer not in text, answer[:80]
    assert sc.title not in text
    for tag in sc.tags:
        assert f'"{tag}"' not in text and tag not in text.split()


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["scenario_id"])
def test_task_is_well_formed(row):
    messages = row["responses_create_params"]["input"]
    assert [m["role"] for m in messages] == ["system", "user"]
    digest = hashlib.sha256((messages[0]["content"] + "\n" + messages[1]["content"]).encode())
    assert digest.hexdigest() == row["prompt_sha256"]
    assert '"recommendation"' in messages[1]["content"]


def test_answer_corpus_is_not_empty():
    assert len(_scripted_answers()) > 40
    assert len(_verdict_labels()) > 40


def test_builder_reads_no_answer_files(monkeypatch):
    """Building every task never opens a file under proposals/, patches/ or examples/."""
    import builtins
    from pathlib import Path

    opened = []
    real_open, real_read = builtins.open, Path.read_text

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_read(self, *a, **k):
        opened.append(str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    for sid in sorted(scenarios.SCENARIOS):
        tasks.build_task(sid, hits=[])
    bad = [p for p in opened if any(s in p for s in ("/proposals/", "/patches/", "/examples/"))]
    assert not bad


@pytest.mark.skipif(not all(WORKTREES[s.old_version].is_dir()
                            for s in scenarios.SCENARIOS.values()),
                    reason="scenario old worktrees not set up (uv run migproto setup)")
def test_frozen_tasks_are_current():
    assert tasks.build_all() == ROWS, "run: uv run python gym/build_tasks.py"
