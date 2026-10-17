"""Review-request parsing owned by the agent controller."""

import json
from copy import deepcopy
from pathlib import Path

from ..config import SALEOR_COMMIT

CONTRACT_VERSION = "migproto.contracts/v1"
TOPOLOGIES = {"roles", "single"}

DEFAULT_REQUEST = {
    "contract_version": CONTRACT_VERSION,
    "request_id": "default-user-note-rename",
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


def load_review_request(value: str | Path | dict | None, source: str,
                        topology: str | None = None) -> dict:
    if value is None:
        request = deepcopy(DEFAULT_REQUEST)
    elif isinstance(value, dict):
        request = deepcopy(value)
    else:
        request = json.loads(Path(value).read_text())
    if not isinstance(request, dict):
        raise ValueError("review request must be a JSON object")
    if request.get("contract_version", CONTRACT_VERSION) != CONTRACT_VERSION:
        raise ValueError(f"review request contract_version must be {CONTRACT_VERSION!r}")
    change = request.get("change")
    if not isinstance(change, dict) or not isinstance(change.get("description"), str):
        raise ValueError("review request change.description must be a string")
    chosen = topology or request.get("agent_topology", "roles")
    if chosen not in TOPOLOGIES:
        raise ValueError(f"agent_topology must be one of {sorted(TOPOLOGIES)}")
    request["contract_version"] = CONTRACT_VERSION
    request["mode"] = source
    request["agent_topology"] = chosen
    request.setdefault("budget", {})
    return request


def scenario_text(request: dict) -> str:
    target = request.get("target", {})
    change = request["change"]
    versions = request.get("app_versions", {})
    return (
        f"Target: {target.get('app', 'application')} at {target.get('commit', 'unknown commit')}.\n"
        f"Change under review: {change['description']}\n"
        f"Table: {change.get('table', 'unknown')}; old column: "
        f"{change.get('old_column', 'unknown')}; new column: "
        f"{change.get('new_column', 'unknown')}.\n"
        f"Application versions: old={versions.get('old', 'old')}, "
        f"new={versions.get('new', 'new')}.\n"
    )
