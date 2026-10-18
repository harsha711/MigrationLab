"""Frozen single-step tasks: one per scenario in migproto/scenarios.py.

The prompt is assembled only from the agent-side facts the controller gives its planner
(migproto/agents/facts.py, migproto/agents/roles.py, migproto/agents/request.py) plus the
runner's deterministic code search of the scenario's OLD app version (roles.gather_hits).
It never reads proposals/manual/, proposals/scripted/, patches/ or examples/, and it leaves
out the scenario title and tags (titles such as "... with duplicates" and the tag
"safe-control" are hints the controller does not show either).

Single-step: the policy sees the prompt once and answers with one plan; there is no tester
role and no revision. The controller's investigator step is replaced by the raw search hits.
"""

import hashlib
import json
from pathlib import Path

from migproto import scenarios
from migproto.agents import facts, roles
from migproto.agents.request import scenario_request, scenario_text
from migproto.config import WORKTREES

TASK_VERSION = 1
TASKS_FILE = Path(__file__).resolve().parents[1] / "tasks" / "migration_review.jsonl"

# rename-note keeps its original contract text (roles.PLAN_CONTRACT), which predates the
# recommendation field; the other scenarios' contract (facts.contract_text) already has it.
RECOMMENDATION_TEXT = """\
Also set "recommendation" in the plan object: "accept_as_is" (safe to deploy directly: deploy_new
then retire_old, no SQL stages), "staged_rollout" (needs your SQL stages around the deploy), or
"reject" (no plan keeps both versions correct; still submit the closest plan so the runner records
evidence, and say why in summary).
"""

ANSWER_FORMAT = """\
Answer with the plan as ONE JSON object and nothing else (no prose, no Markdown outside it).
The runner executes your plan exactly once on a disposable database with both app versions
running; there is no revision step in this task. The runner, not you, decides pass/fail.
"""


def search_hits(sid: str) -> list[dict]:
    """The deterministic search the controller runs before its investigator call."""
    sfacts = facts.for_scenario(sid, roles.SEARCH_ROOTS)
    if sfacts.is_default:
        return roles.gather_hits()
    old = facts.old_version(sid)
    return roles.gather_hits(WORKTREES[old], pattern=facts.search_regex(sfacts.search_terms))


def user_prompt(sid: str, hits: list[dict]) -> str:
    sfacts = facts.for_scenario(sid, roles.SEARCH_ROOTS)
    request = scenario_request(sid)
    terms = ", ".join(f"`{t}`" for t in sfacts.search_terms)
    body = "\n\n".join(f"### {h['ref']}\n{h['context']}" for h in hits)
    parts = [
        scenario_text(request),
        sfacts.contract,
        RECOMMENDATION_TEXT if sfacts.is_default else "",
        "The runner ALWAYS runs this mandatory catalogue right after deploy_new (both versions "
        f"live):\n{facts.render_cases(sfacts.cases)}\n",
        f"The runner searched the OLD application code for {terms} (whole words, excluding "
        f"migrations) under {roles.SEARCH_ROOTS}. Search hits ({len(hits)}):\n{body}\n",
        ANSWER_FORMAT,
    ]
    return "\n".join(p for p in parts if p)


def build_task(sid: str, hits: list[dict] | None = None) -> dict:
    scenarios.get(sid)   # unknown ids raise KeyError
    hits = search_hits(sid) if hits is None else hits
    user = user_prompt(sid, hits)
    return {
        "task_id": f"migproto-{sid}",
        "scenario_id": sid,
        "task_version": TASK_VERSION,
        "prompt_sha256": hashlib.sha256(
            (roles.PLANNER_SYSTEM + "\n" + user).encode()).hexdigest(),
        "responses_create_params": {
            "input": [
                {"role": "system", "content": roles.PLANNER_SYSTEM},
                {"role": "user", "content": user},
            ],
        },
    }


def build_all() -> list[dict]:
    return [build_task(sid) for sid in sorted(scenarios.SCENARIOS)]


def write(rows: list[dict], path: Path = TASKS_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return path


def load(path: Path = TASKS_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
