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
    assert status == 200 and b"Replay Recorded Run" in body
    assert b"Guided demo" in body and b"evidence-drawer" in body
    assert b"welcome-tour" in body and b'id="toast"' in body
    data = get_json(base_url + "/api/runs")
    assert data["controller_available"] is False
    assert {r["run_id"] for r in data["runs"]} == {
        "unsafe-rename", "scripted-sync-failure", "scripted-staged-success", "live-ordering-revision"}


def test_run_and_observation_endpoints(base_url):
    run = get_json(base_url + "/api/run?id=live-ordering-revision")
    assert [a["attempt"] for a in run["attempts"]] == ["a1", "a2"]
    m = get_json(base_url + "/api/matrix?run=scripted-sync-failure&attempt="
                 + quote("scripted-sync-failure/a1", safe=""))
    oid = next(r["observation_id"] for r in m["rows"] if r["check_name"] == "case.old_write_new_read")
    o = get_json(base_url + "/api/observation?id=" + quote(oid, safe=""))
    assert o["status"] == "FAIL" and o["schema_state"] is None


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
        assert post({"mode": "scripted"})[0] == 409
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
    assert targets == {"migproto.agents.controller", "migproto.lock", "migproto.runlog"}
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
    assert "editorial-layout" in css and "Migration evidence" in js
