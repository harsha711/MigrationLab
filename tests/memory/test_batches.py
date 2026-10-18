"""Batches view: read-only supervisor records from runs/brev-batch-*/ and their runs' files."""

import json
import re
from pathlib import Path

import pytest

from migproto.memory import query, store

ROOT = Path(__file__).resolve().parents[2]
BATCHES = Path(__file__).parent / "fixtures" / "batches"
BATCH = "brev-batch-20260101-100000"


def by_n(detail):
    return {r["n"]: r for r in detail["runs"]}


def test_list_batches_newest_first_without_run_lists():
    batches = query.list_batches(BATCHES)
    assert [b["batch_id"] for b in batches] == [BATCH, "brev-batch-20260101-090000"]
    assert all("runs" not in b for b in batches)
    empty = batches[1]
    assert empty["has_runs_jsonl"] is False and empty["run_count"] == 0
    assert empty["config_note"] == "smoke test; supervisor stopped before any review"
    assert batches[0]["config_note"].startswith("FIXTURE CONFIGURATION")


def test_counts_per_scenario_and_topology_exclude_infrastructure_failures():
    b = query.batch_detail(BATCHES, BATCH)
    assert b["unreadable_lines"] == 1 and b["run_count"] == 5 and b["model_result_count"] == 4
    cells = {(c["scenario"], c["topology"]): c for c in b["counts"]}
    assert set(cells) == {("drop-column", "roles"), ("rename-note", "roles"),
                          ("rename-note", "single")}
    pick = lambda c: [c[v] for v in query.BATCH_VERDICTS]
    # Run 3 (ERROR, named in INFRA-NOTE) is not counted; run 5 (ERROR, not named) is.
    assert pick(cells[("rename-note", "roles")]) == [1, 0, 0, 0]
    assert pick(cells[("rename-note", "single")]) == [0, 0, 1, 1]
    # runs.jsonl says UNKNOWN with no scenario; the run's summary.json is preferred.
    assert pick(cells[("drop-column", "roles")]) == [0, 1, 0, 0]
    assert [r["n"] for r in b["infrastructure_failures"]] == [3]
    assert by_n(b)[3]["infrastructure_failure"] and not by_n(b)[1]["infrastructure_failure"]


def test_infra_note_stamps():
    note = (BATCHES / BATCH / "INFRA-NOTE").read_text()
    # The short form takes the previous date; "brev-batch-<stamp>" is a batch, not a run.
    assert query.infra_run_stamps(note, "20260101") == {"20260101-100300", "20260101-100350"}
    assert query.infra_run_stamps("runs -100300 and -100400", "20260102") == {
        "20260102-100300", "20260102-100400"}
    assert query.infra_run_stamps("outage 19:43-20:11, port 8100") == set()
    assert query.infra_run_stamps(None) == set()


def test_per_run_fields_keep_every_attempt():
    conn = store.connect(":memory:")
    conn.execute("INSERT INTO runs (run_id) VALUES ('20260101-100100-agent-loop-llm')")
    runs = by_n(query.batch_detail(BATCHES, BATCH, conn))
    two = runs[2]
    assert [a["attempt"] for a in two["attempts"]] == ["a1", "a2", "a3", "a10"]
    assert [a["fail"] for a in two["attempts"]] == [16, 16, 13, 0]
    assert [(a["refused"], a["error"]) for a in two["attempts"][:2]] == [(1, 0), (0, 1)]
    assert (two["total_tokens"], two["model_calls"], two["wall_seconds"]) == (310400, 13, 899.0)
    assert two["recommendation"] is None and two["runner_recommendation"] == "reject"
    assert two["stopped"].startswith("token budget exhausted")
    one = runs[1]
    assert one["trace_url"] == "https://langfuse.example/project/p/traces/fixture-trace"
    assert one["in_store"] is True and two["in_store"] is False and two["trace_url"] is None
    assert one["recommendation_agrees"] is True and runs[4]["recommendation_agrees"] is False
    assert two["recommendation_recorded"] is True and runs[3]["recommendation_recorded"] is False
    assert runs[3]["attempts"] == []  # recorded but empty: no attempt executed
    assert runs[5]["attempts"] is None  # no summary.json: not recorded
    five = runs[5]
    assert five["run_id"] is None and five["has_summary"] is False
    assert five["verdict"] == "ERROR" and five["verdict_source"] == "runs.jsonl"


@pytest.mark.parametrize("bad", ["../x", "runs", "brev-batch-20260101-100000/../x",
                                 "brev-batch-19990101-000000", None, ""])
def test_batch_detail_rejects_unknown_or_unsafe_ids(bad):
    assert query.batch_detail(BATCHES, bad) is None


def test_batch_run_ids_cannot_leave_runs_root(tmp_path):
    runs_root = tmp_path / "runs"
    batch = runs_root / "brev-batch-20260101-000000"
    batch.mkdir(parents=True)
    (tmp_path / "summary.json").write_text(json.dumps({"verdict": "PASS"}))
    (batch / "runs.jsonl").write_text(json.dumps({"n": 1, "run_id": ".."}) + "\n"
                                      + json.dumps({"n": 2, "run_id": "a/../.."}) + "\n")
    runs = query.batch_detail(runs_root, batch.name)["runs"]
    assert [r["has_summary"] for r in runs] == [False, False]


def snapshot(path: Path):
    return {p.relative_to(path).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(path.rglob("*")) if p.is_file()}


def test_batches_are_read_only():
    conn = store.connect(":memory:")
    store.ingest_all(conn, sources=(ROOT / "examples",))
    before_rows = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                   for t in ("runs", "observations", "claims", "events")]
    before = snapshot(BATCHES)
    query.list_batches(BATCHES, conn)
    query.batch_detail(BATCHES, BATCH, conn)
    assert snapshot(BATCHES) == before
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("runs", "observations", "claims", "events")] == before_rows


def test_batches_never_read_answer_labels_or_scripted_plans():
    sources = [(ROOT / "migproto" / "memory" / "query.py").read_text(encoding="utf-8"),
               (ROOT / "migproto" / "ui" / "server.py").read_text(encoding="utf-8"),
               (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")]
    for source in sources:
        assert not re.search(r"""["'/]proposals\b""", source)
        assert "score_scenarios" not in source and "expected_verdict" not in source
