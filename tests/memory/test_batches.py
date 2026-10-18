"""Batches view: read-only supervisor records from runs/brev-batch-*/ and their runs' files."""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from migproto.memory import query, store

ROOT = Path(__file__).resolve().parents[2]
BATCHES = Path(__file__).parent / "fixtures" / "batches"
EXPECTED = Path(__file__).parent / "fixtures" / "expected"
BATCH = "brev-batch-20260101-100000"
LIVE = "brev-batch-20260101-235800"
NOW = datetime(2026, 1, 2, 0, 10, 0)


def by_n(detail):
    return {r["n"]: r for r in detail["runs"]}


def test_list_batches_newest_first_without_run_lists():
    batches = query.list_batches(BATCHES)
    assert [b["batch_id"] for b in batches] == [LIVE, BATCH, "brev-batch-20260101-090000"]
    assert all("runs" not in b for b in batches)
    empty = batches[2]
    assert empty["has_runs_jsonl"] is False and empty["run_count"] == 0
    assert empty["config_note"] == "smoke test; supervisor stopped before any review"
    assert batches[1]["config_note"].startswith("FIXTURE CONFIGURATION")


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


def test_answer_labels_only_in_scoring_and_never_on_the_agent_path():
    read = lambda *p: ROOT.joinpath("migproto", *p).read_text(encoding="utf-8")
    query_src = read("memory", "query.py")
    # query.py reads proposals/manual/scenarios through EXPECTED_VERDICTS and nothing else.
    assert re.findall(r'"proposals"[^\n]*', query_src) == [
        '"proposals" / "manual" / "scenarios"']
    assert not re.search(r"""["'/]scripted\b""", query_src)
    assert query.EXPECTED_VERDICTS == ROOT / "proposals" / "manual" / "scenarios"
    for source in (read("ui", "server.py"), read("ui", "static", "app.js")):
        assert not re.search(r"""["'/]proposals\b""", source)
    # Agent-facing code never reaches the scoring.
    agent_side = [read("memory", "retrieval.py")] + [
        p.read_text(encoding="utf-8") for p in (ROOT / "migproto" / "agents").glob("*.py")]
    for source in agent_side:
        assert "expected_target" not in source and "EXPECTED_VERDICTS" not in source
        assert "list_batches" not in source and "batch_detail" not in source


# ---- live progress from supervisor.log ----

def test_running_batch_shows_started_runs_without_a_row_and_their_elapsed_time():
    b = query.batch_detail(BATCHES, LIVE, now=NOW, expected_root=EXPECTED)
    assert b["status"] == "running" and b["planned_runs"] == 4 and b["running_count"] == 1
    # Run 3 started after midnight: the log date advanced to the next day.
    assert b["in_progress"] == [{
        "n": 3, "scenario": "drop-column", "topology": "roles", "worker": 1,
        "started_at": "2026-01-02T00:02:01", "status": "running", "elapsed_seconds": 479}]
    assert b["log_last_line_at"] == "2026-01-02T00:03:00"
    assert [r["n"] for r in b["runs"]] == [1, 2]  # running runs are not result rows
    assert sum(c[v] for c in b["counts"] for v in query.BATCH_VERDICTS) == 2


def test_batch_is_finished_only_with_a_done_line(tmp_path):
    b = query.batch_detail(BATCHES, BATCH, now=NOW)
    assert b["status"] == "finished" and b["running_count"] == 0
    # Run 6 started but its runs.jsonl line is unreadable: after DONE it is not "running".
    assert [(r["n"], r["status"], r["elapsed_seconds"]) for r in b["in_progress"]] == [
        (6, "no_result", None)]
    batch = tmp_path / "brev-batch-20260101-120000"
    batch.mkdir()
    (batch / "runs.jsonl").write_text(json.dumps({"n": 1, "verdict": "PASS"}) + "\n")
    assert query.batch_detail(tmp_path, batch.name)["status"] == "unknown"  # no supervisor.log
    (batch / "supervisor.log").write_text(
        "12:00:01 RUN   1/1 roles starting (~$1.00)\n"
        "12:05:00 RUN   1/1 roles PASS run=x 299 s (~$1.10)\n"
        "12:05:01 INFO  DONE is only a level, not text\n")
    assert query.batch_detail(tmp_path, batch.name)["status"] == "running"
    with (batch / "supervisor.log").open("a") as f:
        f.write("12:05:02 DONE  1 runs\n")
    assert query.batch_detail(tmp_path, batch.name)["status"] == "finished"


def test_supervisor_progress_old_format_and_missing_date(tmp_path):
    (tmp_path / "supervisor.log").write_text(
        "18:21:22 RUN   1/2 roles starting (~$2.07)\n"
        "18:21:37 RUN   1/2 roles ERROR run=20261017-182122-agent-loop-llm 15.1 s (~$2.09)\n"
        "not a log line\n18:21:37 RUN   2/2 single starting (~$2.09)\n")
    p = query.supervisor_progress(tmp_path, None)
    assert p["finished"] is False and p["planned"] == 2 and p["last_line_at"] is None
    assert [(s["scenario"], s["topology"], s["worker"], s["started_at"])
            for s in p["started"].values()] == [(None, "roles", None, None),
                                                (None, "single", None, None)]
    assert query.supervisor_progress(tmp_path / "missing") is None


# ---- scoring against the manual expected verdicts ----

def test_expected_target_follows_score_scenarios_rule():
    assert query.expected_target("add-table", EXPECTED) == {
        "verdict": "PASS", "recommendations": ["accept_as_is"]}
    assert query.expected_target("drop-column", EXPECTED) == {
        "verdict": "PASS", "recommendations": ["staged_rollout"]}
    assert query.expected_target("unique-last-name", EXPECTED) == {
        "verdict": "FAIL", "recommendations": ["reject"]}
    for bad in ("rename-note", "../expected/add-table", None, ""):
        assert query.expected_target(bad, EXPECTED) is None


def test_scenario_scores_exclude_infrastructure_and_unscored_runs():
    live = query.batch_detail(BATCHES, LIVE, now=NOW, expected_root=EXPECTED)
    assert [(r["n"], r["matches_expected"]) for r in live["runs"]] == [(1, True), (2, True)]
    assert live["scenario_score"] == {"matched": 2, "scored": 2}
    b = query.batch_detail(BATCHES, BATCH, now=NOW, expected_root=EXPECTED)
    runs = by_n(b)
    # drop-column: runner FAIL/reject, target PASS/staged_rollout.
    assert runs[4]["matches_expected"] is False
    assert runs[4]["expected"] == {"verdict": "PASS", "recommendations": ["staged_rollout"]}
    # rename-note has no expected file; run 3 is an infrastructure failure.
    assert all(runs[n]["matches_expected"] is None for n in (1, 2, 3, 5))
    assert runs[3]["expected"] is None
    assert b["scenario_score"] == {"matched": 0, "scored": 1}
    cell = {(c["scenario"], c["topology"]): c for c in b["counts"]}[("drop-column", "roles")]
    assert (cell["matched"], cell["scored"]) == (0, 1)


def test_shared_run_directory_is_flagged_and_not_scored(tmp_path):
    # Two batches started a run in the same second; the later summary.json replaced the first.
    run = "20260101-110000-agent-loop-llm"
    (tmp_path / run / "agents").mkdir(parents=True)
    (tmp_path / run / "summary.json").write_text(json.dumps(
        {"verdict": "FAIL", "scenario": "rename-note", "topology": "roles",
         "runner_recommendation": "reject", "outcome_counts": {f"{run}/a1": {"fail": 3}}}))
    (tmp_path / run / "agents" / "trace.json").write_text(json.dumps({"url": "https://x/t"}))
    rows = {"brev-batch-20260101-100000": {"scenario": "add-table", "verdict": "PASS"},
            "brev-batch-20260101-100001": {"scenario": "rename-note", "verdict": "FAIL"}}
    for batch, row in rows.items():
        (tmp_path / batch).mkdir()
        (tmp_path / batch / "runs.jsonl").write_text(json.dumps(
            {"n": 1, "run_id": run, "topology": "roles", "total_tokens": 5, **row}) + "\n")
    first = query.batch_detail(tmp_path, "brev-batch-20260101-100000", expected_root=EXPECTED)
    r = first["runs"][0]
    assert r["summary_conflict"] == ["scenario", "verdict"]
    assert r["shared_with"] == [{"batch_id": "brev-batch-20260101-100001", "n": 1}]
    # The other run's summary and trace are not shown; the supervisor's row is.
    assert (r["verdict"], r["verdict_source"], r["total_tokens"]) == ("PASS", "runs.jsonl", 5)
    assert r["attempts"] is None and r["trace_url"] is None and r["has_summary"] is False
    assert r["expected"] is not None and r["matches_expected"] is None
    assert first["scenario_score"] == {"matched": 0, "scored": 0}
    second = query.batch_detail(tmp_path, "brev-batch-20260101-100001")["runs"][0]
    assert second["summary_conflict"] == [] and second["has_summary"] is True
    assert second["shared_with"] == [{"batch_id": "brev-batch-20260101-100000", "n": 1}]
    assert second["matches_expected"] is None


def test_infrastructure_failures_are_never_scored(tmp_path):
    (tmp_path / "rename-note.json").write_text(json.dumps(
        {"plans": {"safe.json": {"verdict": "PASS", "recommendation": "staged_rollout"}}}))
    runs = by_n(query.batch_detail(BATCHES, BATCH, now=NOW, expected_root=tmp_path))
    assert runs[1]["matches_expected"] is True
    assert runs[2]["matches_expected"] is False and runs[5]["matches_expected"] is False
    assert runs[3]["infrastructure_failure"] and runs[3]["matches_expected"] is None
    assert runs[3]["expected"] is None
