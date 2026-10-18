"""UI server serves read-only JSON; memory/UI code has no execution paths."""

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from migproto.memory import store
from migproto.ui import server

ROOT = Path(__file__).resolve().parents[2]
BATCH_FIXTURES = Path(__file__).parent / "fixtures" / "batches"


@pytest.fixture(scope="module")
def base_url():
    conn = store.connect(":memory:")
    store.ingest_all(conn, sources=(ROOT / "examples",))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(conn, threading.Lock()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def get_json(url):
    return json.loads(get(url)[1])


def test_static_and_runs(base_url):
    status, body = get(base_url + "/")
    assert status == 200 and b"Understand migration evidence" in body
    assert b"Ask evidence" in body and b"evidence-drawer" in body
    assert b"welcome-tour" in body and b'id="toast"' in body
    data = get_json(base_url + "/api/runs")
    assert data["controller_available"] is False
    assert {r["run_id"] for r in data["runs"]} == {
        "unsafe-rename", "scripted-sync-failure", "scripted-staged-success", "live-ordering-revision"}
    assert all("attempt_count" in run and "checks_failed" in run for run in data["runs"])
    activities = get_json(base_url + "/api/scenario-activities")["activities"]
    assert isinstance(activities, list)


def test_run_and_observation_endpoints(base_url):
    run = get_json(base_url + "/api/run?id=live-ordering-revision")
    assert [a["attempt"] for a in run["attempts"]] == ["a1", "a2"]
    m = get_json(base_url + "/api/matrix?run=scripted-sync-failure&attempt="
                 + quote("scripted-sync-failure/a1", safe=""))
    oid = next(r["observation_id"] for r in m["rows"] if r["check_name"] == "case.old_write_new_read")
    o = get_json(base_url + "/api/observation?id=" + quote(oid, safe=""))
    assert o["status"] == "FAIL" and o["schema_state"] is None


def test_scenarios_endpoint_only_exposes_picker_fields(base_url):
    scenarios = get_json(base_url + "/api/scenarios")["scenarios"]
    assert scenarios and set(scenarios[0]) == {"id", "title", "description"}
    rendered = json.dumps(scenarios).lower()
    assert "expected" not in rendered and "proposal" not in rendered and "patch" not in rendered


def test_no_traversal_and_no_run_review(base_url):
    for path in ("/../store.py", "/..%2Fserver.py", "/nope.html"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base_url + path)
        assert e.value.code == 404
    req = urllib.request.Request(base_url + "/api/review", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 503


def test_review_endpoints_over_http(tmp_path):
    from review_fakes import FakeLock, FakeRunLog, make_controller
    from migproto.ui.review import ReviewManager

    conn = store.connect(":memory:")
    runs = tmp_path / "runs"
    runs.mkdir()
    lock = threading.Lock()
    gate = threading.Event()
    review = ReviewManager(conn, lock, runs_root=runs, controller=make_controller(gate),
                           lock_module=FakeLock(),
                           runlog_factory=lambda command: FakeRunLog(runs, command))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(conn, lock, review))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(body):
        req = urllib.request.Request(base + "/api/review", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert get_json(base + "/api/review/status")["ok"] is True
        assert post({"mode": "shell"})[0] == 400
        status, started = post({"mode": "scripted", "topology": "roles"})
        assert status == 202
        assert post({"mode": "scripted"})[0] == 202
        jobs = get_json(base + "/api/jobs")["jobs"]
        assert len(jobs) == 2 and jobs[1]["state"] in {"queued", "running", "finished"}
        events = get_json(base + "/api/review/events?run=" + started["run_id"])
        assert events["events"] and events["done"] is False
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base + "/api/review/events?run=..%2Fx")
        assert e.value.code == 400
    finally:
        gate.set()
        httpd.shutdown()


def test_review_module_loads_only_allowed_cross_owner_modules():
    source = (ROOT / "migproto" / "ui" / "review.py").read_text(encoding="utf-8")
    targets = set(re.findall(r"import_module\(\"([\w.]+)\"\)", source))
    assert targets == {"migproto.agents.controller", "migproto.agents.request", "migproto.lock", "migproto.runlog"}
    # Lazy only: no module-level import of other owners' code.
    assert not re.search(r"^(import|from)\s+(migproto\.)?(agents|lock|runlog|\.\.(agents|lock|runlog))",
                         source, re.M)


def test_memory_and_ui_have_no_execution_paths():
    forbidden = re.compile(r"^\s*(import|from)\s+(psycopg|subprocess|requests|migproto\.(db|staged|"
                           r"saleor_app|agents|setup_env|checks)|\.\.(db|staged|saleor_app|agents|"
                           r"setup_env|checks)\b)", re.M)
    for path in list((ROOT / "migproto" / "memory").glob("*.py")) + \
            list((ROOT / "migproto" / "ui").glob("*.py")):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path


def test_hackathon_ui_assets_are_evidence_first():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "GUIDED RECORDED REPLAY" in js
    assert "Final tested outcome" in js
    assert "openEvidence" in js
    assert "evidence-drawer" in css
    assert "prefers-reduced-motion" in css
    assert "INTERACTIVE COMPATIBILITY REVIEW" in js
    assert "FAILED PLAN &#8594; REVISED PLAN" in js and "The failure stays visible" in js
    assert "workspace-layout" in css
    assert "data-workspace-row" in js
    assert "migrationlab-tour-seen" in js
    assert "data-reveal-revision" in js
    assert "celebration-spark" in css
    assert "editorial-layout" in css and "Browse migration reviews" in js


def test_first_use_navigation_is_task_oriented():
    html = (ROOT / "migproto" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'href="#/ask" data-nav="ask"' in html
    assert 'data-nav="runs" class="primary-nav"' not in html
    assert "Ask evidence" in html and "Browse runs" in html
    assert "Track batches" in html and "Compare approaches" in html
    assert "What do you want to understand?" in js
    assert "How to read these answers" in js
    assert 'parts[0] === "topologies" ? "topologies"' in js


def test_run_library_does_not_hydrate_every_run():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    page = js[js.index("async function pageRuns()"):js.index("function replaySteps")]
    assert "Promise.all" not in page
    assert "getRun(" not in page
    assert "Browse migration reviews" in page
    assert "detailed observations load only after you open a run" in page


def test_run_library_is_a_clear_entry_point_to_evidence():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "Find a review" in js and "Open its evidence" in js and "Compare carefully" in js
    assert "INCOMPLETE" in js
    assert ".run-library-guide" in css and ".run-quick-link" in css


def test_topology_page_has_descriptive_resource_profile():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "RECORDED RESOURCE PROFILE" in js
    assert "Token footprint" in js and "Tokens per call" in js
    assert "does not determine which approach is better" in js
    assert ".resource-metric-grid" in css


def test_activity_library_assets_are_scenario_first():
    html = (ROOT / "migproto" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'href="#/activities" data-nav="activities"' in html
    assert "SCENARIO-FIRST EVIDENCE" in js
    assert "MODEL & TOPOLOGY PERFORMANCE" in js
    assert "/api/scenario-activities" in js


def test_compatibility_checks_use_plain_language_before_the_grid():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert '"Compatibility checks"' in js
    assert "COMPATIBILITY SUMMARY" in js and "WHAT NEEDS ATTENTION" in js
    assert "Old app" in js and "Show all compatibility checks" in js
    assert ".compat-summary-grid" in css and ".compat-failure-grid" in css
    assert "grid-template-rows: 14px minmax(38px, auto) 32px 1fr" in css
    assert ".compat-failure > *" in css and "text-indent: 0" in css
    assert "justify-content: stretch" in css


def test_compatibility_matrix_does_not_equate_green_paths_with_attempt_success():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    page = js[js.index("async function pageMatrix"):js.index("async function pageAgents")]
    assert "A green matrix does not override a failed attempt outcome." in page
    assert "The shown app interactions passed, but another runner check did not" in page
    assert "Other runner checks" in page


def test_run_overview_has_an_evidence_first_revision_story():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "How the proposed migration changed after runner feedback" in js
    assert "Planner proposed" in js and "Runner observed" in js and "What changed" in js
    assert "Different recorded check coverage" in js
    assert "proposal, not runner evidence" in js
    assert ".revision-story-card" in css and ".runner-observed" in css


def test_agents_page_explains_role_handoffs_before_raw_claims():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "migproto" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "Who did what, and what did the runner confirm?" in js
    assert "Planner produced" in js and "Tester handed back" in js and "Runner recorded" in js
    assert "Only runner observations determine pass/fail." in js
    assert "roles do not share chat history directly" in js
    assert ".agent-role-flow" in css and ".agent-handoff-grid" in css


def test_batch_endpoints_over_http():
    batch = "brev-batch-20260101-100000"
    handler = server.make_handler(store.connect(":memory:"), threading.Lock(),
                                  runs_root=BATCH_FIXTURES)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert batch in [b["batch_id"] for b in get_json(base + "/api/batches")["batches"]]
        detail = get_json(base + f"/api/batch?id={batch}")
        assert detail["infrastructure_failures"][0]["run_id"] == "20260101-100300-agent-loop-llm"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/batch?id=..%2Fexamples")
        assert e.value.code == 404
    finally:
        httpd.shutdown()


def test_batches_page_assets():
    js = (ROOT / "migproto" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "migproto" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="#/batches"' in html
    assert "pageBatches" in js and "pageBatch(" in js
    assert "Infrastructure failures" in js and "not model results" in js
    assert "Langfuse trace" in js and 'rel="noopener noreferrer"' in js
    assert "scored against manual expected verdicts (never shown to agents)" in js
    assert "Running now" in js and "BATCH_POLL_MS = 20000" in js
    # Polling only re-reads: no non-GET request anywhere near the batch pages.
    assert "method:" not in js[js.index("// ---------- Brev batches"):js.index("async function openEvidence")]
