# Team Guide

Three owners, one shared runner, one contract file. Read the root `README.md` and `AGENTS.md` first, then your assignment.

| Owner | Area | Assignment | Branch |
|---|---|---|---|
| 1, Harsha | runner, verification, contracts coordination | [01-runner.md](01-runner.md) | `runner/verification-v1` |
| 2 | agents and orchestration | [02-agents.md](02-agents.md) | `agents/small-model-loop` |
| 3 | scoped memory and review UI | [03-memory-ui.md](03-memory-ui.md) | `memory-ui/scoped-review` |

## Ground rules

- `main` holds the working prototype (tag `feasibility-v1`). Keep `uv run pytest` and `uv run migproto agent-loop --proposals scripted` green on every merge.
- Each owner edits only their owned paths. Changes to someone else's module go through a short note to that owner (or a PR they review).
- Shared contracts live in `docs/contracts.md`. Harsha coordinates every change. Propose changes in your handoff note with the field, producer, consumer and a JSON example.
- Use `examples/` as the frozen integration fixtures. Do not commit `runs/`. Promote a new fixture with `uv run python scripts/export_example.py runs/<run_id> <name> --kind <kind>` (Harsha reviews).
- Evaluated agents must never see `proposals/scripted/plan_v2.json`, `proposals/manual/`, `patches/`, held-out scenarios or `examples/` outcomes.

## Bounties and priority

Bounty owners: Nemotron 3.5 Lightning and Red Hat vLLM go to Owner 2, NeMo Gym goes to Harsha, and evidence presentation goes to Owner 3. Requirements, status and open eligibility questions are in [../bounties.md](../bounties.md), and the parameter budget (≤ 33B total) is tracked in [../model-inventory.md](../model-inventory.md). Priority: (1) small-model loop on vLLM, (2) integrated evidence and UI, (3) NeMo Gym adapter and evaluation, (4) optional serving experiments. GPU work uses the event-provided NVIDIA Brev credits only.

## Integration checklist

1. **Freeze example events and contracts.** `examples/` plus `docs/contracts.md` v0 are frozen at `feasibility-v1`. v1 fields are additive. (Harsha)
2. **Runner → evidence adapter.** Runner emits v1 scope fields. The memory adapter ingests `examples/` and new runs into SQLite. (Harsha + Owner 3)
3. **Agents → runner and scoped retrieval.** The controller calls the stable runner interface and reads prior observations through the memory query API. (Owner 2 + Harsha + Owner 3)
4. **UI → recorded runs.** "Replay Recorded Run" renders `examples/` and local `runs/` through the adapter. (Owner 3)
5. **UI → live review.** "Run Review" calls the controller, which calls the runner. The UI never executes SQL or starts servers itself. (Owner 3 + Owner 2)
6. **Small-model single-agent versus multi-agent comparison.** Same scenario, tools, evidence access, execution limits and total budget, repeated runs, held-out variation. Report results as measured, with no superiority claim beyond them. (Owner 2, scenario from Harsha)

## Handoff note template

Put this in your PR description or `docs/team/handoff-<owner>.md`:

```
Owner / branch:
What changed (paths):
Verification run (commands + run_ids):
Contract changes proposed (field, producer, consumer, JSON example):
Known gaps / blockers for other owners:
```
