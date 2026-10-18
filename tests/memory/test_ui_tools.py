"""Pre-runner agent tools (schema profile, dry run, planner fix) surfaced read-only by the UI."""

import json
import shutil
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from migproto.memory import store
from migproto.ui import server, tools

BATCH_FIXTURES = Path(__file__).parent / "fixtures" / "batches"
STATIC = Path(server.__file__).parent / "static"

PROFILE = {"table": "account_user", "rows": 245, "columns": [
    {"column": "note", "type": "text", "not_null": False, "default": None, "rows": 245,
     "null_values": 54, "distinct_non_null_values": 191, "max_char_length": 2000}],
    "constraints": [], "indexes": []}
PLAN = {"plan_id": "a1-p", "stages": [
    {"id": "expand", "kind": "sql", "phase": "expand",
     "sql": ["ALTER TABLE account_user ADD COLUMN customer_note varchar(10)"]}]}
FIXED = {"plan_id": "a1-p-pf", "stages": [
    {"id": "expand", "kind": "sql", "phase": "expand",
     "sql": ["ALTER TABLE account_user ADD COLUMN customer_note text"]}]}
REPORT_1 = {"stages": [{"stage": "expand", "kind": "sql", "phase": "expand", "result": "error",
                        "error": "value too long for type character varying(10)"}],
            "probes": [{"point": "before deploy_new", "table": "account_user",
                        "columns_present": ["note", "customer_note"],
                        "writes": [{"write": "UPDATE account_user SET note = 'p1' WHERE id = <first row>",
                                    "before": {"note": "x", "customer_note": "x"},
                                    "after": {"note": "p1", "customer_note": "x"}}]}],
            "first_error": {"stage": "expand", "error": "value too long for type character varying(10)"}}
REPORT_2 = {"stages": [{"stage": "expand", "kind": "sql", "phase": "expand", "result": "executed"}],
            "probes": [{"point": "before deploy_new", "skipped": "table has no rows"}],
            "first_error": None}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _k_run(run_dir: Path, *, invalid_second=True, drop_fixed_plan=False) -> Path:
    agents = run_dir / "agents"
    _write(agents / "schema_profile.json", PROFILE)
    _write(agents / "preflight-1.json", REPORT_1)
    _write(agents / "preflight-2.json", REPORT_2)
    _write(agents / "planner-1.json", PLAN)
    if not drop_fixed_plan:
        _write(agents / "planner-1-preflight.json", FIXED)
    events = [
        {"t": 0.2, "kind": "tool", "name": "schema_profile", "msg": "read-only S0 profile"},
        {"t": 5.0, "kind": "tool", "name": "preflight", "attempt": 1, "plan_id": "a1-p",
         "first_error": REPORT_1["first_error"], "msg": "dry run (not a verdict)"},
        {"t": 9.0, "kind": "proposal", "role": "planner", "attempt": 1, "plan_id": "a1-p-pf",
         "parent_plan_id": "a1-p", "preflight_fix": True},
        {"t": 30.0, "kind": "tool", "name": "preflight", "attempt": 2, "plan_id": "a2-q",
         "first_error": None, "msg": "dry run (not a verdict)"},
    ]
    if invalid_second:
        events.append({"t": 31.0, "kind": "preflight_fix_invalid", "attempt": 2,
                       "plan_id": "a2-q", "error": "stage 3: id must be unique"})
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + '\n{"partial', encoding="utf-8")
    return run_dir


def test_run_tools_reads_profile_dry_runs_and_fix(tmp_path):
    out = tools.run_tools(_k_run(tmp_path / "r"))
    assert out["schema_profile"]["rows"] == 245
    a1, a2 = out["attempts"]
    assert a1["attempt"] == 1 and a1["report"]["first_error"]["stage"] == "expand"
    assert a1["fix"]["parent_plan_id"] == "a1-p" and a1["fix"]["plan_id"] == "a1-p-pf"
    assert any(l.startswith("-ALTER") and "varchar(10)" in l for l in a1["fix"]["diff"])
    assert any(l.startswith("+ALTER") and l.endswith("text") for l in a1["fix"]["diff"])
    assert a2["fix"] is None and a2["fix_invalid"]["error"].startswith("stage 3")
    assert [e["name"] for e in out["tool_events"]] == ["schema_profile", "preflight", "preflight"]
    assert tools.summary(tmp_path / "r") == {"dry_runs": 2, "errors_found": 1, "fixes": 1,
                                             "fixes_invalid": 1}
    # Nothing in the tool output carries a verdict.
    def keys(x):
        return (set(x) | {k for v in x.values() for k in keys(v)} if isinstance(x, dict)
                else {k for v in x for k in keys(v)} if isinstance(x, list) else set())
    assert not keys(out) & {"verdict", "ok", "status", "outcome"}


def test_fix_without_both_plan_files_has_no_diff(tmp_path):
    out = tools.run_tools(_k_run(tmp_path / "r", drop_fixed_plan=True))
    assert out["attempts"][0]["fix"]["diff"] is None


def test_run_with_none_of_the_files(tmp_path):
    run = tmp_path / "old"
    run.mkdir()
    (run / "events.jsonl").write_text(json.dumps({"kind": "check", "name": "x"}) + "\n")
    assert tools.run_tools(run) == {"schema_profile": None, "attempts": [], "tool_events": []}
    assert tools.summary(run) is None
    assert tools.run_tools(tmp_path / "missing")["attempts"] == []


def _serve(runs_root):
    handler = server.make_handler(store.connect(":memory:"), threading.Lock(), runs_root=runs_root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def test_tools_endpoint_and_batch_dry_run_column(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(BATCH_FIXTURES, runs)
    batch = "brev-batch-20260101-100000"
    k_id = None
    for line in (runs / batch / "runs.jsonl").read_text().splitlines():
        try:
            k_id = json.loads(line)["run_id"]
            break
        except (ValueError, KeyError):
            continue  # the fixture holds a deliberately truncated row
    _k_run(runs / k_id)
    httpd, base = _serve(runs)
    try:
        out = _get_json(base + f"/api/runs/{k_id}/tools")
        assert out["schema_profile"]["table"] == "account_user" and len(out["attempts"]) == 2
        none = _get_json(base + "/api/runs/does-not-exist/tools")
        assert none["attempts"] == [] and none["schema_profile"] is None
        detail = _get_json(base + f"/api/batch?id={batch}")
        by_id = {r.get("run_id"): r for r in detail["runs"]}
        assert by_id[k_id]["dry_run"]["errors_found"] == 1 and by_id[k_id]["dry_run"]["fixes"] == 1
        assert all(r["dry_run"] is None for rid, r in by_id.items() if rid != k_id)
        for bad in ("..%2Fexamples", "a%2Fb"):
            try:
                urllib.request.urlopen(base + f"/api/runs/{bad}/tools")
                assert False, "path traversal accepted"
            except urllib.error.HTTPError as e:
                assert e.code == 404
    finally:
        httpd.shutdown()


def test_assets_label_dry_run_as_not_a_verdict():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "DRY RUN · NOT A VERDICT" in js and "STARTING DATABASE" in js
    assert "Plan fixed after dry run" in js and "dry-run errors found / fixes" in js
    assert 'event.kind === "tool"' in js
    # Probe rows are never styled with the pass/fail classes used for runner checks.
    probe = js[js.index("function probeTable"):js.index("function dryRunPanel")]
    assert "verdictBadge" not in probe and "PASS" not in probe and "FAIL" not in probe


def test_malformed_investigator_finding_does_not_break_ingest():
    # A K run (20261018-104946) recorded findings with a bare string; ingest must not crash.
    from migproto.memory import adapter
    run = {"run_id": "r", "events": [], "agent_files": {"investigator": {"findings": [
        "saleor/account/models.py:182",
        {"ref": "a.py:1", "kind": "write", "needs_change": True, "reason": "writes note"}]}}}
    out = adapter.claims(run)
    assert [c["text"] for c in out] == ["a.py:1: writes note"]
