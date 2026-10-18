"""Stdlib HTTP server for the "Replay Recorded Run" UI.

Replay is read-only: static files plus JSON from the evidence store. "Run Review" starts a
review only through Owner 2's controller (see review.py) and is disabled, with the reason,
when the controller or Harsha's execution lock is unavailable. Evidence chat may make one
budgeted call through the existing model client after releasing the store lock. Nothing here
executes SQL against Saleor databases or starts Saleor servers.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..memory import query, store
from .. import scenarios
from . import tools
from .review import Busy, ReviewManager, Unavailable

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}
MAX_BODY = 16384
RUN_ID = re.compile(r"\w[\w.-]*\Z")
BATCH_ID = re.compile(r"brev-batch-[\w.-]+\Z")


def _read_json(path: Path):
    """Best-effort read of an immutable run artifact; malformed means unknown."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _batch_records(path: Path) -> list[dict]:
    records = []
    try:
        lines = (path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return records
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


def _configuration(path: Path) -> str | None:
    try:
        note = (path / "CONFIG-NOTE").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"\bCONFIGURATION\s+([A-Z0-9_-]+)\b", note, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _value(*values):
    return next((value for value in values if value is not None), None)


def _profile(runs_root: Path, batch_path: Path | None, row: dict | None,
             run_id: str) -> dict:
    """Recorded context only. Missing values intentionally stay None/unknown."""
    run_dir = runs_root / run_id
    summary, request = _read_json(run_dir / "summary.json") or {}, _read_json(run_dir / "request.json") or {}
    totals = summary.get("agent_totals") if isinstance(summary.get("agent_totals"), dict) else {}
    budget = request.get("budget") if isinstance(request.get("budget"), dict) else {}
    row = row or {}
    return {
        "run_id": run_id,
        "batch_id": batch_path.name if batch_path else None,
        "configuration": _configuration(batch_path) if batch_path else None,
        "scenario": _value(row.get("scenario"), summary.get("scenario"), request.get("scenario")),
        "topology": _value(row.get("topology"), summary.get("topology"), request.get("agent_topology")),
        "model": summary.get("model"),
        "thinking_budget": summary.get("thinking_budget"),
        "token_budget": _value(totals.get("max_total_tokens"), budget.get("max_total_tokens")),
        "wall_budget": _value(totals.get("max_wall_seconds"), budget.get("max_wall_seconds")),
        "has_finished_evidence": (run_dir / "summary.json").is_file(),
    }


def counterpart(runs_root, run_id: str, conn=None) -> dict | None:
    """Find same-batch, same-scenario, same-derived-repetition topology counterparts.

    `runs.jsonl` has no explicit repetition field.  The Brev supervisor emits rows in launch
    order: repetition -> scenario -> topology, with a monotonic `n`.  We therefore derive a
    repetition index as the one-based occurrence of a (scenario, topology) pair after sorting
    those records by `n`; it is never inferred when the scenario/topology scope is absent.
    """
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        return None
    root = Path(runs_root)
    matches = []
    for path in root.glob("brev-batch-*"):
        if not path.is_dir() or not BATCH_ID.fullmatch(path.name):
            continue
        for row in _batch_records(path):
            if row.get("run_id") == run_id:
                matches.append((path, row))
    if len(matches) != 1:
        profile = _profile(root, None, None, run_id)
        return {"run": profile, "counterparts": [],
                "reason": "not part of a batch" if not matches else "run appears in multiple batch logs"}

    path, row = matches[0]
    profile = _profile(root, path, row, run_id)
    scenario, topology = profile["scenario"], profile["topology"]
    if not isinstance(scenario, str):
        return {"run": profile, "counterparts": [], "reason": "scenario is not recorded"}
    if not isinstance(topology, str):
        return {"run": profile, "counterparts": [], "reason": "topology is not recorded"}

    records = _batch_records(path)
    ordered = sorted(enumerate(records), key=lambda item: (
        item[1].get("n") if isinstance(item[1].get("n"), int) else 10 ** 12, item[0]))
    occurrence, repetition = {}, None
    decorated = []
    for _, candidate in ordered:
        candidate_id = candidate.get("run_id")
        candidate_profile = _profile(root, path, candidate,
                                     candidate_id if isinstance(candidate_id, str) else "")
        key = (candidate_profile["scenario"], candidate_profile["topology"])
        if all(isinstance(value, str) for value in key):
            occurrence[key] = occurrence.get(key, 0) + 1
            candidate_profile["repetition"] = occurrence[key]
        else:
            candidate_profile["repetition"] = None
        if candidate.get("run_id") == run_id and candidate.get("n") == row.get("n"):
            repetition = candidate_profile["repetition"]
        decorated.append(candidate_profile)
    profile["repetition"] = repetition
    if repetition is None:
        return {"run": profile, "counterparts": [],
                "reason": "repetition cannot be derived from the recorded batch row"}

    options = [candidate for candidate in decorated
               if candidate["scenario"] == scenario and candidate["topology"] != topology
               and candidate["repetition"] == repetition]
    ready = [candidate for candidate in options
             if candidate["run_id"] and candidate["has_finished_evidence"]]
    if not options:
        reason = f"no other topology was recorded for scenario {scenario!r}, repetition {repetition}"
    elif not ready:
        reason = "a counterpart was recorded, but its finished run evidence is not available"
    else:
        reason = None
    detail = query.batch_detail(root, path.name, conn)
    rows_by_id = {item.get("run_id"): item for item in (detail or {}).get("runs", [])}
    for candidate in ready:
        # Expected outcomes are evaluation-only metadata already used by the existing batch UI.
        row_detail = rows_by_id.get(candidate["run_id"], {})
        candidate["expected"] = row_detail.get("expected")
        candidate["matches_expected"] = row_detail.get("matches_expected")
    own_detail = rows_by_id.get(run_id, {})
    profile["expected"] = own_detail.get("expected")
    profile["matches_expected"] = own_detail.get("matches_expected")
    return {"run": profile, "counterparts": ready, "reason": reason}


def make_handler(conn, lock, review: ReviewManager | None = None, runs_root=None, chat_answer=None):
    runs_root = Path(runs_root) if runs_root is not None else store.ROOT / "runs"
    from .chat import build_facts
    if chat_answer is None:
        from .chat import answer as chat_answer

    def availability():
        value = review.available() if review else {"ok": False, "reason": "controller not available"}
        return {**value, "llm_available": bool(os.environ.get("MIGPROTO_LLM_MODEL"))}

    def run_dir(run_id):
        if not run_id or not RUN_ID.match(run_id):
            return None
        path = (runs_root / run_id).resolve()
        return path if path.parent == runs_root.resolve() and path.is_dir() else None

    def with_dry_runs(detail):
        # Agent-side dry-run counts per run row, read from the run directory (never a verdict).
        for row in (detail or {}).get("runs") or []:
            path = run_dir(row.get("run_id"))
            row["dry_run"] = tools.summary(path) if path else None
        return detail

    def api(path, params):
        p = lambda name: (params.get(name) or [None])[0]
        if path.startswith("/api/runs/") and path.endswith("/tools"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/tools").rstrip("/")
            if not RUN_ID.match(run_id or ""):
                raise KeyError(path)
            path_ = run_dir(run_id)
            return {"run_id": run_id, **(tools.run_tools(path_) if path_ else
                    {"schema_profile": None, "attempts": [], "tool_events": []})}
        if path.startswith("/api/runs/") and path.endswith("/counterpart"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/counterpart").rstrip("/")
            result = counterpart(runs_root, run_id, conn)
            if result is None:
                raise KeyError(path)
            return result
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
                return with_dry_runs(query.batch_detail(runs_root, p("id"), conn))
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
