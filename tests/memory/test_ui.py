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
    assert "PLAN COMPARISON" in js and "Why did the first attempt fail?" in js
    assert "workspace-layout" in css
    assert "data-workspace-row" in js
    assert "migrationlab-tour-seen" in js
    assert "data-reveal-revision" in js
    assert "celebration-spark" in css
    assert "Recorded migration review" in js
    assert "Revised plan passed" in js
    assert "Restart replay" in js
    assert "trapFocus" in js
    assert "scope=\"col\"" in js and "scope=\"row\"" in js
    assert "progressive-card" in css
    assert "data-nav" in js and "navKey" in js
    assert "Seamless dashboard theme" in css
    assert "REVIEW COMMAND CENTER" in js
    assert "data-attempt-select" in js
    assert "command-palette" in js
    assert "workspaceAttempt" in js
    assert "Rebuilt dashboard command center" in css
