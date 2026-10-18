"""Scoped prior evidence for the agent controller (team README: Owner 3 -> Owner 2).

The controller calls `prior_evidence(change, model=...)` before planning and passes the result to the
planner as context. The store only reports what earlier runs observed; the controller
decides what happens next and the runner decides pass/fail.

Selection rules are hard-coded here on purpose (AGENTS.md: never expose examples/ outcomes,
scripted solutions or answer labels to evaluated agents). There are no flags to relax them:
- only finished local runs under runs/ are ingested and considered; examples/ never is
- only runs whose provenance is `agent:<model>`, so scripted / manual plans (plan_v2) never
  reach a prompt
- only runs of the calling run's own model (provenance exactly `agent:<model>`), so a local
  stand-in model's runs never reach a Nemotron prompt and vice versa
- only runs whose recorded review request names the same table, old column and new column;
  a run without a request has unknown scope and is excluded, never assumed to match
- with `scenario`, only runs whose request names that scenario id; a request without a
  `scenario` field has unknown scope and is excluded
- the calling run itself is excluded
"""

import json

from . import store
from .query import query_observations

BAD_OUTCOMES = {"fail", "refused", "error"}


def _same_change(request: dict | None, change: dict) -> bool:
    theirs = (request or {}).get("change") or {}
    keys = ("table", "old_column", "new_column")
    return all(change.get(k) is not None and theirs.get(k) == change.get(k) for k in keys)


def _is_bad(o: dict) -> bool:
    return o["outcome"] in BAD_OUTCOMES if o["outcome"] else o["status"] == "FAIL"


def _item(o: dict, plan_summaries: dict) -> dict:
    raw = o["raw"] or {}
    return {
        "observation_id": o["observation_id"],
        "run_id": o["run_id"],
        "attempt_id": o["attempt_id"],
        "plan_id": o["plan_id"],
        "plan_summary": plan_summaries.get((o["run_id"], o["plan_id"])),
        "stage_id": o["stage_id"],
        "check_name": o["check_name"],
        "status": o["status"],
        "outcome": o["outcome"],
        "check_type": o["check_type"],
        "app_scope": o["app_scope"],
        "app_versions": o["app_versions"],
        "schema_hash": (o["schema_state"] or {}).get("schema_hash"),
        "msg": raw.get("msg"),
        "error": o["error"],
        "expected": o["expected"],
        "observed": o["observed"],
    }


def prior_evidence(change: dict, *, model: str, scenario: str | None = None, db=None,
                   runs_root=None, exclude_run_id=None, limit: int = 20) -> dict:
    """Failed, refused and errored observations from earlier agent runs of the same change.

    `change` is the review request's `change` object (needs `table`, `old_column`,
    `new_column`). `model` is the calling run's model name as recorded in its provenance
    (`agent:<model>`); only runs of exactly that model are used. `scenario` optionally
    restricts to runs whose review request names the same scenario id. Returns the
    observations plus `runs_considered` and `excluded` counts so the selection can be audited.
    """
    if not isinstance(model, str) or not model:
        raise ValueError("prior_evidence: `model` must be the calling run's model name")
    conn = store.connect(db)
    try:
        store.ingest_all(conn, sources=(runs_root or store.ROOT / "runs",))
        excluded = {"fixtures": 0, "non_agent": 0, "different_model": 0,
                    "unknown_or_different_change": 0, "unknown_or_different_scenario": 0,
                    "calling_run": 0}
        chosen = []
        for r in conn.execute("SELECT run_id, source_kind, provenance, request_json, verdict "
                              "FROM runs ORDER BY run_id DESC"):
            if r["source_kind"] != "local_run":
                excluded["fixtures"] += 1
            elif r["run_id"] == exclude_run_id:
                excluded["calling_run"] += 1
            elif not str(r["provenance"] or "").startswith("agent:"):
                excluded["non_agent"] += 1
            elif r["provenance"] != f"agent:{model}":
                excluded["different_model"] += 1
            else:
                request = json.loads(r["request_json"]) if r["request_json"] else None
                if not _same_change(request, change):
                    excluded["unknown_or_different_change"] += 1
                elif scenario is not None and (request or {}).get("scenario") != scenario:
                    excluded["unknown_or_different_scenario"] += 1
                else:
                    chosen.append(r)

        plan_summaries = {}
        observations, runs_considered = [], []
        for r in chosen:
            rid = r["run_id"]
            for p in conn.execute("SELECT plan_id, body_json FROM plans WHERE run_id = ?", (rid,)):
                body = json.loads(p["body_json"]) if p["body_json"] else {}
                plan_summaries[(rid, p["plan_id"])] = body.get("summary")
            attempts = [{"attempt_id": a["attempt_id"], "plan_id": a["plan_id"],
                         "status": a["status"]}
                        for a in conn.execute("SELECT attempt_id, plan_id, status FROM attempts "
                                              "WHERE run_id = ? ORDER BY attempt", (rid,))]
            runs_considered.append({"run_id": rid, "verdict": r["verdict"], "attempts": attempts})
            observations += [_item(o, plan_summaries)
                             for o in query_observations(conn, run_id=rid, source_kind="local_run")
                             if _is_bad(o)]
        return {
            "change": {k: change.get(k) for k in ("table", "old_column", "new_column")},
            "model": model,
            "scenario": scenario,
            "observations": observations[:limit],
            "truncated": len(observations) > limit,
            "runs_considered": runs_considered,
            "excluded": excluded,
        }
    finally:
        conn.close()
