"""Stdlib HTTP server for the "Replay Recorded Run" screen.

Read-only: serves static files and JSON from the evidence store. It never executes SQL
against Saleor databases, starts Saleor servers, or calls a model. "Run Review" stays
disabled until the controller / runner interface exists (docs/team/03-memory-ui.md).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..memory import query, store

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}
CONTROLLER_AVAILABLE = False


def make_handler(conn, lock):
    def api(path, params):
        p = lambda name: (params.get(name) or [None])[0]
        with lock:
            if path == "/api/runs":
                return {"runs": query.list_runs(conn), "controller_available": CONTROLLER_AVAILABLE}
            if path == "/api/run":
                return query.run_detail(conn, p("id"))
            if path == "/api/matrix":
                return query.matrix(conn, p("run"), p("attempt"))
            if path == "/api/observation":
                return query.get_observation(conn, p("id"))
            if path == "/api/history":
                return {"check_name": p("check"), "rows": query.check_history(conn, p("check"))}
        raise KeyError(path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body: bytes, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                try:
                    result = api(url.path, parse_qs(url.query))
                except KeyError:
                    return self._json(404, {"error": "unknown endpoint"})
                if result is None:
                    return self._json(404, {"error": "not found"})
                return self._json(200, result)
            name = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
            target = STATIC / name
            # Only plain files directly inside static/ (no traversal).
            if "/" in name or "\\" in name or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, target.read_bytes(),
                       CONTENT_TYPES.get(target.suffix, "application/octet-stream"))

        def do_POST(self):
            # Run Review is not wired: the UI must go through the controller only.
            self._json(503, {"error": "controller not available"})

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(host="127.0.0.1", port=8765, db=None) -> None:
    conn = store.connect(db)
    ingested = store.ingest_all(conn)
    server = ThreadingHTTPServer((host, port), make_handler(conn, threading.Lock()))
    print(f"evidence store: {db or store.DEFAULT_DB} ({len(ingested)} runs ingested)")
    print(f"Replay Recorded Run: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
