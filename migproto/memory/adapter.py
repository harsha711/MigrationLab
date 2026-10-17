"""Read a run directory and normalize it into scoped records.

Applies only the official v0 derivation rules from docs/contracts.md and marks derived
values `scope_source = "derived_v0"`. Anything the rules do not cover stays `None`
(unknown). v1 records (with `contract_version`) pass their own fields through.

Pure functions, no database. `load(run_dir)` is the entry point.
"""

import json
import re
from pathlib import Path

V1 = "migproto.contracts/v1"
DEV_ONLY_MODELS = {"gpt-oss:120b-cloud"}

# Agent JSON files and the free-text fields that hold their (unstructured, v0) claims.
CLAIM_FIELDS = {
    "planner": ("write_consistency", "revision_notes"),
    "tester-report": ("summary", "suspected_cause"),
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_run(run_dir) -> dict:
    """Load the raw files of one run directory. Missing files are `None` / empty."""
    d = Path(run_dir)
    events_path = d / "events.jsonl"
    if not events_path.is_file():
        raise FileNotFoundError(f"{d} has no events.jsonl")
    events = []
    for seq, line in enumerate(events_path.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            events.append({"seq": seq, **json.loads(line)})
    opt = lambda name: _read_json(d / name) if (d / name).is_file() else None
    agents_dir = d / "agents"
    sql_dir = d / "sql"
    return {
        "run_id": d.name,  # v0 rule 1
        "path": str(d),
        "source_kind": "fixture" if d.parent.name == "examples" else "local_run",
        "summary": opt("summary.json"),
        "manifest": opt("manifest.json"),
        "events": events,
        "plans": {p.name: _read_json(p) for p in sorted(d.glob("plan-*.json"))},
        "agent_files": ({p.stem: _read_json(p) for p in sorted(agents_dir.glob("*.json"))}
                        if agents_dir.is_dir() else {}),
        "sql_files": ({p.name: p.read_text(encoding="utf-8") for p in sorted(sql_dir.glob("*.sql"))}
                      if sql_dir.is_dir() else {}),
    }


def _attempt_label(event: dict) -> str | None:
    m = re.search(r"(\d+)", str(event.get("name", "")))
    return f"a{m.group(1)}" if m else None


def _app_version(app: str | None, command: str | None) -> str | None:
    """v0 rule 5: map a step's `app` field to a worktree name."""
    if app == "old":
        return "old"
    if app == "new":
        return "new-unsafe" if command == "unsafe" else "new-staged"
    return None


def derive(run: dict) -> dict:
    """Walk events once and attach scope to every check (v0 rules 2-5)."""
    run_id = run["run_id"]
    summary = run["summary"] or {}
    command = summary.get("command")
    events = run["events"]
    has_attempts = any(e["kind"] == "attempt" for e in events)

    # v0 rule 2: no attempt events -> exactly one attempt a1. In loop files, lines before
    # the first attempt event (investigator) are not covered by the rules -> unknown.
    attempt = None if has_attempts else "a1"
    attempt_plan = {}  # attempt -> plan_id
    stage = "S0" if attempt else None
    stage_kind = None

    # v0 rule 4: schema state of a SQL stage = the schema event emitted for it.
    schema_by_stage = {}
    cur = None
    for e in events:
        if e["kind"] == "attempt":
            cur = _attempt_label(e)
        elif e["kind"] == "schema":
            schema_by_stage[(cur if has_attempts else "a1", e.get("stage"))] = {
                "note_columns": e.get("note_columns"), "triggers": e.get("triggers")}

    attempts, stages, observations, others = {}, [], [], []
    for e in events:
        kind = e["kind"]
        if kind == "attempt":
            attempt = _attempt_label(e)
            stage, stage_kind = "S0", None  # v0 rule 3, per attempt
            attempt_plan[attempt] = e.get("plan_id")
            attempts[attempt] = {"attempt": attempt, "plan_id": e.get("plan_id"),
                                 "status": None, "failed": None}
        elif kind == "attempt_result":
            label = _attempt_label(e)
            if label in attempts:
                attempts[label]["status"] = e.get("status")
                attempts[label]["failed"] = e.get("failed")
        elif kind == "stage":
            stage, stage_kind = e.get("stage"), e.get("stage_kind")
            stages.append({"attempt": attempt, "stage_id": stage, "stage_kind": stage_kind,
                           "phase": e.get("phase"), "description": e.get("description"),
                           "seq": e["seq"]})

        if kind != "check":
            others.append({"run_id": run_id, "attempt": attempt, "stage_id": stage,
                           "seq": e["seq"], "kind": kind, "t": e.get("t"), "raw": e})
            continue

        if e.get("contract_version") == V1:
            obs = _from_v1(run_id, e, attempt, attempt_plan.get(attempt))
        else:
            plan_id = e.get("plan_id") or attempt_plan.get(attempt)
            schema = (schema_by_stage.get((attempt, stage))
                      if stage_kind == "sql" else None)
            obs = {
                "observation_id": f"{run_id}/{attempt or '-'}/{e['name']}#{e['seq']}",
                "run_id": run_id,
                "attempt": attempt,
                "plan_id": plan_id,
                "stage_id": stage,
                "check_name": e["name"],
                "status": e.get("status"),
                "outcome": None,       # v0 cannot tell fail / refused / error apart (G2)
                "check_type": None,
                "app_scope": None,
                "app_commit": None,
                "database_snapshot_id": None,
                "expected": None,
                "observed": None,
                "schema_state": schema,
                "definition_provenance": e.get("provenance"),
                "executed_by": "runner",
                "scope_source": "derived_v0",
                "seq": e["seq"],
                "error": e.get("error"),
                "raw": e,
            }
        obs["steps"] = [
            {"step": i, "app_version": _app_version(s.get("app"), command), "op": s.get("op"),
             "user": s.get("user"), "ok": s.get("ok"), "value": s.get("value"),
             "error": s.get("error")}
            for i, s in enumerate(e.get("steps") or [])
        ]
        observations.append(obs)

    if not has_attempts:
        # One attempt executes exactly one plan: summary.plan_id (staged) or none (unsafe).
        plan_id = summary.get("plan_id") or next(
            (o["plan_id"] for o in observations if o["plan_id"]), None)
        attempts["a1"] = {"attempt": "a1", "plan_id": plan_id,
                          "status": summary.get("verdict"), "failed": summary.get("checks_failed")}
        for o in observations:
            if o["plan_id"] is None and o["attempt"] == "a1":
                o["plan_id"] = plan_id
    for s in summary.get("attempts") or []:
        label = f"a{s.get('attempt')}"
        if label in attempts:
            attempts[label].update(ok=s.get("ok"), halted=s.get("halted"),
                                   failed=s.get("failed_checks", attempts[label]["failed"]))
    if not has_attempts and "a1" in attempts:
        attempts["a1"].update(ok=summary.get("ok"), halted=summary.get("halted"))

    return {"attempts": list(attempts.values()), "stages": stages,
            "observations": observations, "events": others}


def _from_v1(run_id: str, e: dict, attempt: str | None, plan_id: str | None) -> dict:
    """Pass the runner's explicit v1 fields through (shape: docs/team/handoff-01.md).

    The store keys runs by directory name (v0 rule 1), so the attempt is normalized to
    `<directory>/a<N>`; the recorded `run_id` / `attempt_id` stay in `raw`. The recorded
    `observation_id` is kept as the primary key so agent claims can cite it.
    """
    attempt_id = e.get("attempt_id")
    label = attempt_id.rsplit("/", 1)[-1] if attempt_id else attempt
    return {
        "observation_id": e.get("observation_id") or f"{run_id}/{label or '-'}/{e['name']}#{e['seq']}",
        "run_id": run_id,
        "attempt": label,
        "plan_id": e.get("plan_id") or plan_id,
        "stage_id": e.get("step_id", e.get("stage_id")),  # recorded; absent -> unknown
        "check_name": e.get("name"),
        "status": e.get("status"),
        "outcome": e.get("outcome"),
        "check_type": e.get("check_type"),
        "app_scope": e.get("app_version"),  # old / new / both / database / None, set by the runner
        "app_commit": e.get("app_commit"),
        "database_snapshot_id": e.get("database_snapshot_id"),
        "expected": e.get("expected"),
        "observed": e.get("observed"),
        "schema_state": e.get("schema_state"),
        "definition_provenance": e.get("definition_provenance", e.get("provenance")),
        "executed_by": e.get("executed_by"),
        "scope_source": "recorded_v1",
        "seq": e["seq"],
        "error": e.get("error"),
        "raw": e,
    }


def plans_by_attempt(run: dict, attempts: list[dict]) -> list[dict]:
    """Match plan files to attempts by plan_id. Unmatched plans keep attempt None."""
    by_id = {a["plan_id"]: a["attempt"] for a in attempts if a.get("plan_id")}
    out = []
    for fname, body in run["plans"].items():
        pid = body.get("plan_id")
        sql_name = fname.replace(".json", ".sql")
        out.append({"plan_id": pid, "attempt": by_id.get(pid), "file": fname,
                    "body": body, "sql": run["sql_files"].get(sql_name)})
    return out


def claims(run: dict) -> list[dict]:
    """Agent claims.

    Structured: `agent_claim` events (Owner 2 controller, v1 claim shape) carry `evidence[]`
    observation IDs. Unstructured (v0): free text from `agents/*.json`, no evidence links.
    A structured tester claim replaces that attempt's free-text tester-report claims.
    """
    run_id = run["run_id"]
    provenance = None
    for e in run["events"]:
        if e["kind"] == "agent_config":
            provenance = e.get("provenance")
            break
    out = []
    structured_tester = set()
    for e in run["events"]:
        if e["kind"] != "agent_claim":
            continue
        m = re.search(r"/(a\d+)/", e.get("claim_id") or "")
        attempt = m.group(1) if m else None
        if e.get("role") == "tester":
            structured_tester.add(attempt)
        out.append({"claim_id": e.get("claim_id") or f"{run_id}/{attempt or '-'}/claim#{e['seq']}",
                    "attempt": attempt, "role": e.get("role"),
                    "kind": e.get("claim_kind"), "text": e.get("text"),
                    "provenance": e.get("provenance"), "structured": 1,
                    "evidence": list(e.get("evidence") or []),
                    "model_call": e.get("model_call")})
    for stem, body in run["agent_files"].items():
        m = re.fullmatch(r"(planner|tester-report)-(\d+)", stem)
        if m:
            role, attempt = m.group(1), f"a{m.group(2)}"
            if role == "tester-report" and attempt in structured_tester:
                continue
            for k, field in enumerate(CLAIM_FIELDS[role]):
                text = (body or {}).get(field)
                if text:
                    out.append({"claim_id": f"{run_id}/{attempt}/{role}/{k}", "attempt": attempt,
                                "role": role, "kind": field, "text": text,
                                "provenance": provenance, "structured": 0, "evidence": []})
        elif stem == "investigator":
            # Investigator ran before any attempt: attempt unknown (None).
            for k, f in enumerate((body or {}).get("findings") or []):
                if not f.get("needs_change"):
                    continue
                out.append({"claim_id": f"{run_id}/-/investigator/{k}", "attempt": None,
                            "role": "investigator", "kind": f.get("kind"),
                            "text": f"{f.get('ref')}: {f.get('reason')}",
                            "provenance": provenance, "structured": 0, "evidence": []})
    return out


def resolve_evidence(run: dict, claim_list: list[dict], observation_ids: set[str]) -> None:
    """Turn each claim's evidence IDs into `{observation_id, resolved}` links.

    An exported fixture's directory name differs from the run_id recorded in the IDs; such
    IDs are rewritten to the directory name when that makes them resolve. IDs that still do
    not match an observation of this run are kept and marked unresolved.
    """
    recorded = run_meta_recorded_id(run)
    for c in claim_list:
        links = []
        for oid in c["evidence"]:
            if oid not in observation_ids and recorded and oid.startswith(recorded + "/"):
                rewritten = run["run_id"] + oid[len(recorded):]
                if rewritten in observation_ids:
                    oid = rewritten
            links.append({"observation_id": oid, "resolved": int(oid in observation_ids)})
        c["evidence"] = links


def run_meta_recorded_id(run: dict) -> str | None:
    summary = run["summary"] or {}
    return summary.get("run_id") or (run["manifest"] or {}).get("source_run_id")


def plan_lineage(run: dict) -> dict:
    """plan_id -> {parent_plan_id, solution_origin} from planner `proposal` events."""
    out = {}
    for e in run["events"]:
        if e["kind"] == "proposal" and e.get("role") == "planner" and e.get("plan_id"):
            out[e["plan_id"]] = {"parent_plan_id": e.get("parent_plan_id"),
                                 "solution_origin": e.get("solution_origin")}
    return out


def run_meta(run: dict) -> dict:
    """Model, serving, budget and request metadata as recorded. Absent -> None."""
    cfg = next((e for e in run["events"] if e["kind"] == "agent_config"), None) or {}
    summary = run["summary"] or {}
    model = cfg.get("model")
    request = run["agent_files"].get("review-request")
    scenario = next((e.get("msg") for e in run["events"] if e["kind"] == "scenario"), None)
    if scenario is None and isinstance(request, dict):
        scenario = (request.get("change") or {}).get("description")
    return {
        "command": summary.get("command"),
        "verdict": summary.get("verdict"),
        "elapsed_s": summary.get("elapsed_s"),
        "checks_total": summary.get("checks_total"),
        "checks_failed": summary.get("checks_failed"),
        "provenance": summary.get("provenance") or cfg.get("provenance"),
        "source": summary.get("source") or cfg.get("source"),
        "model": model,
        "base_url": cfg.get("base_url"),
        "checkpoint_revision": cfg.get("checkpoint_revision") or cfg.get("revision"),
        "serving_backend": cfg.get("serving_backend"),
        "serving_version": cfg.get("serving_version"),
        "hardware": cfg.get("hardware"),
        "driver": cfg.get("driver"),
        "cuda": cfg.get("cuda"),
        "brev_instance": cfg.get("brev_instance"),
        "prefix_caching": cfg.get("prefix_caching"),
        "temperature": cfg.get("temperature"),
        "top_p": cfg.get("top_p"),
        "reasoning_enabled": cfg.get("reasoning_enabled"),
        "solution_origin": cfg.get("solution_origin"),
        "topology": summary.get("topology") or cfg.get("topology"),
        "budget": cfg.get("budget"),
        "agent_totals": summary.get("agent_totals"),
        "reason": summary.get("reason"),
        "request": request if isinstance(request, dict) else None,
        "dev_only_model": model in DEV_ONLY_MODELS,
        "scenario": scenario,
        "contract_version": ((run["manifest"] or {}).get("contract_version")
                             or summary.get("contract_version")
                             # Observations decide: an agent_claim may be v1 while checks are v0.
                             or next((e["contract_version"] for e in run["events"]
                                      if e["kind"] == "check" and e.get("contract_version")), None)
                             or "migproto.events/v0"),
        "recorded_run_id": run_meta_recorded_id(run),
        "start_states": [{k: v for k, v in e.items() if k != "seq"}
                         for e in run["events"] if e["kind"] == "start_state"],
    }


def load(run_dir) -> dict:
    """Read and normalize one run directory into all record types."""
    run = read_run(run_dir)
    derived = derive(run)
    claim_list = claims(run)
    resolve_evidence(run, claim_list, {o["observation_id"] for o in derived["observations"]})
    plans = plans_by_attempt(run, derived["attempts"])
    lineage = plan_lineage(run)
    for p in plans:
        p.update(lineage.get(p["plan_id"]) or {"parent_plan_id": None, "solution_origin": None})
    return {
        "run_id": run["run_id"],
        "path": run["path"],
        "source_kind": run["source_kind"],
        "summary": run["summary"],
        "manifest": run["manifest"],
        "meta": run_meta(run),
        "model_calls": run["agent_files"].get("model_calls") or [],
        "plans": plans,
        "claims": claim_list,
        **derived,
    }
