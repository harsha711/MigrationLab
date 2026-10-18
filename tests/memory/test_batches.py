"""Batches page: Brev batch directories read from files; infrastructure failures never counted."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from migproto.memory import batches, store
from migproto.ui import server

BATCH = "brev-batch-20261017-191348"
OLDER = "brev-batch-20261017-180000"
TRACE_URL = "https://us.cloud.langfuse.com/project/p/traces/abc123"


def _summary(verdict, topology, scenario, attempts, tokens=50_000, calls=6, wall=120.5,
             exhausted=None):
    return {"verdict": verdict, "topology": topology, "scenario": scenario,
            "model": "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
            "elapsed_s": wall + 1, "attempts": attempts,
            "agent_totals": {"total_tokens": tokens, "model_calls": calls, "wall_seconds": wall,
                             "exhausted_reason": exhausted}}


def _attempt(n, ok, failed=()):
    return {"attempt": n, "plan_id": f"a{n}-plan", "ok": ok, "halted": None,
            "failed_checks": list(failed)}


def _run_dir(root, run_id, summary, trace=None):
    d = root / run_id
    (d / "agents").mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(summary))
    if trace is not None:
        (d / "agents" / "trace.json").write_text(json.dumps(trace))


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    b = root / BATCH
    b.mkdir(parents=True)
    # 1: roles PASS after a failed first attempt, traced.
    _run_dir(root, "20261017-191400-agent-loop-llm",
             _summary("PASS", "roles", "rename-note",
                      [_attempt(1, False, ["retirement_gate", "case.old_write_new_read"]),
                       _attempt(2, True)]),
             trace={"provider": "langfuse", "trace_id": "abc123", "url": TRACE_URL,
                    "environment": "brev"})
    # 2: single INCOMPLETE on the token budget, no trace.json.
    _run_dir(root, "20261017-191500-agent-loop-llm",
             _summary("INCOMPLETE", "single", "rename-note", [_attempt(1, False, ["x"])],
                      tokens=300_000, exhausted="tokens"))
    # 3: roles FAIL, but listed in INFRA-NOTE (network outage).
    _run_dir(root, "20261017-191600-agent-loop-llm",
             _summary("FAIL", "roles", "rename-note", [_attempt(1, False, ["a", "b", "c"])]))
    # 4: an A4 run whose trace URL is not http(s).
    _run_dir(root, "20261017-191700-agent-loop-llm",
             _summary("ERROR", "roles", "add-required-column", []),
             trace={"url": "javascript:alert(1)", "trace_id": "t"})
    recs = [
        {"n": 1, "scenario": "rename-note", "topology": "roles", "verdict": "PASS", "exit": 0,
         "run_id": "20261017-191400-agent-loop-llm", "seconds": 130.0},
        {"n": 2, "scenario": "rename-note", "topology": "single", "verdict": "INCOMPLETE",
         "exit": 1, "run_id": "20261017-191500-agent-loop-llm"},
        {"n": 3, "scenario": "rename-note", "topology": "roles", "verdict": "FAIL", "exit": 1,
         "run_id": "20261017-191600-agent-loop-llm"},
        {"n": 4, "scenario": "add-required-column", "topology": "roles", "verdict": "ERROR",
         "exit": 2, "run_id": "20261017-191700-agent-loop-llm"},
        # 5: died before a run dir existed; INFRA-NOTE names it by its .out stem.
        {"n": 5, "scenario": "rename-note", "topology": "single", "verdict": "UNKNOWN",
         "exit": 1, "run_id": None, "seconds": 3.2},
        # 6: run dir missing and not in INFRA-NOTE: counted from runs.jsonl as recorded.
        {"n": 6, "scenario": "rename-note", "topology": "single", "verdict": "UNKNOWN",
         "exit": 1, "run_id": "20261017-192000-agent-loop-llm"},
    ]
    (b / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
    (b / "CONFIG-NOTE").write_text("Configuration A: reasoning cap 16384, 3 revisions, 300k.\n")
    (b / "INFRA-NOTE").write_text(
        "Network outage 19:15-19:20. Excluded: 20261017-191600-agent-loop-llm and "
        "05-rename-note-single (no run dir). Not 20261017-19160 or 5-rename-note-single.\n")
    (b / "environment.txt").write_text("vllm 0.27.1\nH100 80GB\n")
    (root / OLDER).mkdir()  # an empty batch: no runs.jsonl, no notes
    (root / "20261017-191400-agent-loop-llm-not-a-batch").mkdir()
    return root


def test_counts_exclude_infrastructure_failures(runs):
    b = batches.batch_detail(BATCH, runs)
    assert [r["n"] for r in b["runs"]] == [1, 2, 4, 6]
    assert [r["n"] for r in b["infra_runs"]] == [3, 5]
    counts = {(c["scenario"], c["topology"]): c for c in b["counts"]}
    assert counts[("rename-note", "roles")] == {
        "scenario": "rename-note", "topology": "roles", "PASS": 1, "FAIL": 0,
        "INCOMPLETE": 0, "ERROR": 0, "other": 0, "runs": 1}
    assert counts[("rename-note", "single")]["INCOMPLETE"] == 1
    assert counts[("rename-note", "single")]["other"] == 1  # UNKNOWN is kept, not dropped
    assert counts[("add-required-column", "roles")]["ERROR"] == 1
    assert sum(c["runs"] for c in b["counts"]) == 4
    assert b["unparsed_lines"] == 1


def test_run_fields_and_all_attempts(runs):
    b = batches.batch_detail(BATCH, runs)
    first = b["runs"][0]
    assert first["verdict"] == "PASS" and first["verdict_source"] == "summary.json"
    assert (first["total_tokens"], first["model_calls"], first["wall_seconds"]) == (50_000, 6,
                                                                                     120.5)
    assert [(a["attempt"], a["ok"], a["failed_count"]) for a in first["attempts"]] == [
        (1, False, 2), (2, True, 0)]
    assert first["attempts"][0]["failed_checks"] == ["retirement_gate",
                                                     "case.old_write_new_read"]
    assert first["trace"] == {"url": TRACE_URL, "trace_id": "abc123", "environment": "brev"}
    second = b["runs"][1]
    assert second["trace"] is None and second["exhausted_reason"] == "tokens"
    assert b["runs"][2]["trace"]["url"] is None  # javascript: URL is never passed on
    missing = b["runs"][3]
    assert missing["run_dir_found"] is False and missing["verdict_source"] == "runs.jsonl"
    assert missing["attempts"] == []


def test_notes(runs):
    b = batches.batch_detail(BATCH, runs)
    assert b["config_note"].startswith("Configuration A")
    assert "vllm 0.27.1" in b["environment"]
    empty = batches.batch_detail(OLDER, runs)
    assert empty["config_note"] is None and empty["infra_note"] is None
    assert empty["has_runs_jsonl"] is False and empty["runs"] == [] and empty["counts"] == []


def test_list_batches_newest_first(runs):
    out = batches.list_batches(runs)
    assert [b["name"] for b in out] == [BATCH, OLDER]
    assert (out[0]["model_runs"], out[0]["infra_runs"]) == (4, 2)
    assert batches.list_batches(runs / "missing") == []


@pytest.mark.parametrize("name", ["../runs", "brev-batch-../x", "20261017-191400-agent-loop-llm",
                                  "", None])
def test_batch_name_is_validated(runs, name):
    with pytest.raises(ValueError):
        batches.batch_detail(name, runs)


def test_unknown_batch(runs):
    assert batches.batch_detail("brev-batch-nope", runs) is None


def test_read_only(runs):
    before = {p: p.stat().st_mtime_ns for p in runs.rglob("*")}
    batches.list_batches(runs)
    batches.batch_detail(BATCH, runs)
    assert {p: p.stat().st_mtime_ns for p in runs.rglob("*")} == before


def test_batch_endpoints_over_http(runs):
    conn = store.connect(":memory:")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                server.make_handler(conn, threading.Lock(), runs_root=runs))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/batches") as r:
            assert [b["name"] for b in json.loads(r.read())["batches"]] == [BATCH, OLDER]
        with urllib.request.urlopen(base + f"/api/batch?name={BATCH}") as r:
            assert len(json.loads(r.read())["infra_runs"]) == 2
        for query, code in (("name=..%2Fx", 400), ("name=brev-batch-nope", 404)):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/batch?" + query)
            assert e.value.code == code
        # The evidence store is untouched by the Batches page.
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        httpd.shutdown()
