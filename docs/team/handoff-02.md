# Owner 2 Handoff

Owner / branch: Owner 2 / `agents/small-model-loop`

## What changed

- Added the budgeted roles/single controller, review-request parsing, structured vLLM client,
  aggregate telemetry, complete controller-side feedback, and structured agent claims.
- Added `agent-loop --request` and `--topology` wiring and explicit `ERROR` exit code handling.
- Added agent tests and planned-serving/results documentation. No live Lightning deployment or
  qualifying result is claimed.

## Verification

- `python -m compileall -q migproto tests`: passed on the Windows workspace.
- `python -m migproto agent-loop --help`: passed and shows both new flags.
- `uv run pytest -q`: not run here because `uv` is unavailable in this Windows environment.
- `uv run migproto agent-loop --proposals scripted`: not run because the documented runner
  requires the Linux/Docker environment.

## Contract and interface proposals for Harsha

1. CLI exit mapping: `ERROR` now exits 2; `FAIL`, `INCOMPLETE`, and `NOT_REPRODUCED` exit 1;
   successful verdicts exit 0. Consumer: CLI and automation.
2. Execution feedback: Owner 2 currently enriches feedback from `RunLog.checks`. The stable
   runner result should eventually return complete failed/refused/error observation objects.
3. Agent claims use the documented v1 shape and derive v0 observation IDs as
   `<run_id>/a<N>/<check>#<events-line-index>` until native IDs exist.
4. `docs/bounties.md` still says the vLLM integration is not started and the 120B model is the
   client default. Harsha should update it to: client/controller implemented; Brev deployment
   and live Lightning runs pending; no qualifying bounty claim yet.

## Known blockers

- Harsha: stable `runner_api.execute_plan`, native observation IDs, complete outcome fields, and
  review-contract approval.
- Owner 3: scoped query API and UI consumption of the public controller entry point.
- Infrastructure: event-funded Brev GPU, pinned Lightning deployment, and recorded vLLM facts.
