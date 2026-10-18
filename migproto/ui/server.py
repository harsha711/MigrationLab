"""Stdlib HTTP server for the "Replay Recorded Run" UI.

Replay is read-only: static files plus JSON from the evidence store. "Run Review" starts a
review only through Owner 2's controller (see review.py) and is disabled, with the reason,
when the controller or Harsha's execution lock is unavailable. Evidence chat may make one
budgeted call through the existing model client after releasing the store lock. Nothing here
executes SQL against Saleor databases or starts Saleor servers.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..memory import query, store
from .. import scenarios
from .review import Busy, ReviewManager, Unavailable

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}
MAX_BODY = 16384


def make_handler(conn, lock, review: ReviewManager | None = None, runs_root=None, chat_answer=None):
    runs_root = Path(runs_root) if runs_root is not None else store.ROOT / "runs"
    from .chat import build_facts
    if chat_answer is None:
        from .chat import answer as chat_answer

    def availability():
        value = review.available() if review else {"ok": False, "reason": "controller not available"}
        return {**value, "llm_available": bool(os.environ.get("MIGPROTO_LLM_MODEL"))}

    def api(path, params):
        p = lambda name: (params.get(name) or [None])[0]
        if path == "/api/review/status":
            return {**(review.status() if review else {"current": None}), **availability()}
        if path == "/api/review/events":
            if not review:
                return None
            return review.events(p("run"), int(p("after") or 0))
        if path == "/api/jobs":
            return {"jobs": review.jobs() if review else []}
        if path == "/api/scenarios":
            return {"scenarios": [{"id": sc.id, "title": sc.title,
                                   "description": sc.change["description"]}
                                  for sc in scenarios.SCENARIOS.values()]}
        with lock:
            if path == "/api/runs":
                avail = availability()
                return {"runs": query.list_runs(conn), "controller_available": avail["ok"],
                        "controller_reason": avail["reason"]}
            if path == "/api/run":
                return query.run_detail(conn, p("id"))
            if path == "/api/matrix":
                return query.matrix(conn, p("run"), p("attempt"))
            if path == "/api/observation":
                return query.get_observation(conn, p("id"))
            if path == "/api/history":
                return {"check_name": p("check"), "rows": query.check_history(conn, p("check"))}
            if path == "/api/batches":
                return {"batches": query.list_batches(runs_root, conn)}
            if path == "/api/batch":
                return query.batch_detail(runs_root, p("id"), conn)
            if path == "/api/topology-comparison":
                return query.topology_comparison(runs_root, conn)
            if path == "/api/scenario-activities":
                return query.scenario_activity_overview(conn)
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
            self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                try:
                    result = api(url.path, parse_qs(url.query))
                except KeyError:
                    return self._json(404, {"error": "unknown endpoint"})
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
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
            # Always consume the body first: replying early resets the connection on Windows.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                return self._json(413, {"error": "request body too large"})
            raw = self.rfile.read(length) if length else b""
            path = urlparse(self.path).path
            if path == "/api/chat":
                try:
                    body = json.loads(raw or b"{}")
                    question = body.get("question")
                    if not isinstance(question, str) or not question.strip():
                        raise ValueError("question must be a non-empty string")
                    if len(question) > 1000:
                        raise ValueError("question must be at most 1000 characters")
                    with lock:
                        facts = build_facts(question.strip(), conn, runs_root)
                    # The potentially slow model call happens after releasing the DB lock.
                    result = chat_answer(question.strip(), facts=facts)
                    return self._json(200, result)
                except (ValueError, AttributeError) as exc:
                    return self._json(400, {"error": str(exc)})
                except RuntimeError as exc:
                    return self._json(500, {"error": str(exc)})
            if path.startswith("/api/jobs/") and path.endswith("/decision") and review:
                try:
                    body = json.loads(raw or b"{}")
                    run_id = path.removeprefix("/api/jobs/").removesuffix("/decision").rstrip("/")
                    return self._json(200, review.decision(run_id, body.get("decision"),
                                                            body.get("note", "")))
                except (ValueError, AttributeError) as exc:
                    return self._json(400, {"error": str(exc)})
            if path != "/api/review" or review is None:
                return self._json(503, {"error": availability()["reason"]})
            try:
                body = json.loads(raw or b"{}")
                started = review.start(body.get("mode", "scripted"), body.get("topology", "roles"),
                                       body.get("scenario", "rename-note"), body.get("budget"),
                                       {key: body[key] for key in ("continue_from", "human_feedback")
                                        if key in body})
            except Unavailable as exc:
                return self._json(503, {"error": str(exc)})
            except Busy as exc:
                return self._json(409, {"error": str(exc), "owner": exc.owner})
            except (ValueError, AttributeError) as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(500, {"error": str(exc)})
            self._json(202, started)

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(host="127.0.0.1", port=8765, db=None, runs_root=None) -> None:
    """`runs_root` (default runs/) is read for runs to ingest and for brev-batch-* records."""
    runs_root = Path(runs_root) if runs_root is not None else store.ROOT / "runs"
    conn = store.connect(db)
    lock = threading.Lock()
    ingested = store.ingest_all(conn, sources=(store.ROOT / "examples", runs_root))
    review = ReviewManager(conn, lock, runs_root=runs_root)
    server = ThreadingHTTPServer((host, port), make_handler(conn, lock, review, runs_root))
    print(f"evidence store: {db or store.DEFAULT_DB} ({len(ingested)} runs ingested)")
    avail = review.available()
    print(f"Run Review: {'available' if avail['ok'] else 'disabled: ' + avail['reason']}")
    print(f"Replay Recorded Run: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
