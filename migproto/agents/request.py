"""Review-request parsing owned by the agent controller."""

import json
from copy import deepcopy
from pathlib import Path

from .. import scenarios
from ..config import SALEOR_COMMIT

CONTRACT_VERSION = "migproto.contracts/v1"
TOPOLOGIES = {"roles", "single", "parallel"}

DEFAULT_REQUEST = {
    "contract_version": CONTRACT_VERSION,
    "request_id": "default-user-note-rename",
    "scenario": scenarios.DEFAULT,
    "target": {"app": "saleor", "commit": SALEOR_COMMIT},
    "change": {
        "description": (
            "Rename Django field User.note (account_user.note, text NULL) to customer_note. "
            "Keep the public GraphQL field name note."
        ),
        "table": "account_user",
        "old_column": "note",
        "new_column": "customer_note",
        "intentionally_introduced": True,
    },
    "app_versions": {"old": "old", "new": "new-staged"},
    "mode": "scripted",
    "agent_topology": "roles",
    "budget": {
        "max_revisions": 1,
        "max_model_calls": 10,
        "max_total_tokens": 40_000,
        "max_wall_seconds": 600,
    },
}


# Change keys that name schema objects; a request may not contradict its scenario on these.
CHANGE_IDENTIFIERS = ("table", "old_column", "new_column", "new_attribute", "new_table",
                      "indexed_column")


def scenario_request(scenario_id: str) -> dict:
    """The default review request for one scenario of the catalogue (migproto/scenarios.py)."""
    sc = scenarios.get(scenario_id)
    if sc.id == scenarios.DEFAULT:
        return deepcopy(DEFAULT_REQUEST)
    request = deepcopy(DEFAULT_REQUEST)
    request.update(
        request_id=f"default-{sc.id}",
        scenario=sc.id,
        change={**deepcopy(sc.change), "intentionally_introduced": True},
        app_versions={"old": sc.old_version,
                      "new": ", ".join(v.version for v in sc.variants.values())},
    )
    return request


def load_review_request(value: str | Path | dict | None, source: str,
                        topology: str | None = None, scenario: str | None = None) -> dict:
    if value is None:
        try:
            request = scenario_request(scenario or scenarios.DEFAULT)
        except KeyError as exc:
            raise ValueError(f"review request scenario: {exc}") from exc
    elif isinstance(value, dict):
        request = deepcopy(value)
    else:
        request = json.loads(Path(value).read_text())
    if not isinstance(request, dict):
        raise ValueError("review request must be a JSON object")
    if scenario is not None:
        request["scenario"] = scenario
    request.setdefault("scenario", scenarios.DEFAULT)
    try:
        sc = scenarios.get(request["scenario"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"review request scenario: {exc}") from exc
    if "change" not in request:
        request["change"] = {**deepcopy(sc.change), "intentionally_introduced": True}
    if request.get("contract_version", CONTRACT_VERSION) != CONTRACT_VERSION:
        raise ValueError(f"review request contract_version must be {CONTRACT_VERSION!r}")
    change = request.get("change")
    if not isinstance(change, dict) or not isinstance(change.get("description"), str):
        raise ValueError("review request change.description must be a string")
    for key in CHANGE_IDENTIFIERS:
        if key in change and key in sc.change and change[key] != sc.change[key]:
            raise ValueError(f"review request change.{key}={change[key]!r} contradicts "
                             f"scenario {sc.id} ({sc.change[key]!r})")
        if key in sc.change:
            change.setdefault(key, sc.change[key])
    chosen = topology or request.get("agent_topology", "roles")
    if chosen not in TOPOLOGIES:
        raise ValueError(f"agent_topology must be one of {sorted(TOPOLOGIES)}")
    request["contract_version"] = CONTRACT_VERSION
    request["mode"] = source
    request["agent_topology"] = chosen
    request.setdefault("budget", {})
    feedback = request.get("human_feedback", [])
    if not isinstance(feedback, list) or any(
            not isinstance(item, dict) or item.get("kind") not in {"issue", "suggestion", "test"}
            or not isinstance(item.get("text"), str) or not item["text"].strip()
            or not isinstance(item.get("author"), str) or not isinstance(item.get("at"), str)
            for item in feedback):
        raise ValueError("human_feedback must contain kind, text, author and at")
    if feedback:
        request["source_tags"] = sorted(set(request.get("source_tags", [])) | {"human-assisted"})
    continuation = request.get("continue_from")
    if continuation is not None and (not isinstance(continuation, dict)
                                     or not isinstance(continuation.get("run_id"), str)
                                     or not isinstance(continuation.get("attempt"), int)
                                     or continuation["attempt"] < 1):
        raise ValueError("continue_from must contain run_id and a positive attempt")
    return request


def scenario_text(request: dict) -> str:
    target = request.get("target", {})
    change = request["change"]
    versions = request.get("app_versions", {})
    if request.get("scenario", scenarios.DEFAULT) == scenarios.DEFAULT:
        objects = (f"Table: {change.get('table', 'unknown')}; old column: "
                   f"{change.get('old_column', 'unknown')}; new column: "
                   f"{change.get('new_column', 'unknown')}.\n")
    else:
        named = [f"{key.replace('_', ' ')}: {change[key]}"
                 for key in CHANGE_IDENTIFIERS if key in change]
        joined = "; ".join(named)
        objects = joined[:1].upper() + joined[1:] + ".\n"
    return (
        f"Target: {target.get('app', 'application')} at {target.get('commit', 'unknown commit')}.\n"
        f"Change under review: {change['description']}\n"
        + objects +
        f"Application versions: old={versions.get('old', 'old')}, "
        f"new={versions.get('new', 'new')}.\n"
    )
