# Assignment 01: Runner and Verification (Owner 1, Harsha)

## Goal

Make the runner the single trusted source of executed evidence: stable execution interface, fully scoped observations, equivalent starting states, and regression checks for the trigger designs agents generate. Coordinate `docs/contracts.md`.

## Current status (at `feasibility-v1`)

- Working: `setup`, `baseline`, `unsafe`, `staged`, scripted and live `agent-loop`, 34 runner tests.
- Observations are flat `kind: check` lines without run, attempt, stage, app-version or schema fields (contracts G1, G3).
- `FAIL` mixes assertion failures, refusals (`retirement_gate`, `stage.*.preconditions`) and execution errors (`stage.*.executes`) (G2).
- Each attempt clones the same `migproto_seed` template, but start-state equivalence is not recorded (G4).
- `agents/loop.py::_feedback` forwards only some check fields to the planner. The live tester noted the gate failure arrived "without details" (G5).
- The live model's two-trigger sync passed the catalogue but has not been reviewed beyond it.

## Owned paths

`migproto/cli.py`, `migproto/config.py`, `migproto/runlog.py`, `migproto/db.py`, `migproto/setup_env.py`, `migproto/saleor_app.py`, `migproto/seed.py`, `migproto/baseline.py`, `migproto/unsafe.py`, `migproto/plans.py`, `migproto/checks.py`, `migproto/staged.py`, `docker-compose.yml`, `patches/`, `proposals/manual/`, `proposals/negative/`, `tests/test_guard_and_schema.py`, `tests/test_trigger_semantics.py`, `scripts/export_example.py`, `examples/`, `docs/contracts.md`, `REPORT.md`. New: `migproto/runner_api.py`, `heldout/` (access-restricted, see task 8).

## Ordered tasks

1. **Freeze v0.** Confirm `examples/` and `docs/contracts.md` match the code at `feasibility-v1`. Announce the freeze to Owners 2 and 3.
2. **Stable execution interface.** Add `migproto/runner_api.py` with `execute_plan(request: dict, progress=None) -> dict` taking the Execution request contract and returning the per-attempt result (`staged.run` output plus v1 IDs). Keep `staged.run` and the CLI working by calling through it. Allow an optional `progress` callback that receives each event dict as `RunLog.event` writes it.
3. **v1 scope fields (additive).** In `RunLog.check` / `staged.py` / `checks.py`, add `contract_version`, `run_id`, `attempt_id`, `plan_id`, `stage_id`, `observation_id`, `outcome` (`pass` / `fail` / `refused` / `error` / `skipped`), `expectation`, `app_versions_live`, and `schema_state` (introspect after every stage, including `S0`, `deploy_new`, `retire_old`). Keep every v0 key.
4. **Equivalent start states.** Record per attempt: seed checksum (`seed.checksum` over `note` plus a schema hash of `account_user`), worktree heads, SHA-256 of each `patches/<version>` set. Fail the attempt with `outcome: error` if two attempts in one loop start from different states.
5. **Feedback completeness.** Forward all fields of failed or refused observations to the controller (coordinate the `loop._feedback` edit with Owner 2, since it lives in their file).
6. **Trigger review and regression checks.** Review `examples/live-ordering-revision/plan-a2-a2-rename-note-002.json`:
   - **Ordering:** two `BEFORE` triggers fire in name order (`trg_sync_customer_note_to_note` first).
   - **`UPDATE OF <column>` semantics:** the trigger fires when the column is listed in `SET`, not when its value changes.
   - **NULL handling:** INSERT with `OLD` NULL, clears from each side, and backfill skipping `note IS NULL`.
   - **Both-column updates.**

   Then extend `tests/test_trigger_semantics.py` to run the same parametrized cases over every plan file passed in (scripted v1/v2 and the live plan). If a gap needs app-level coverage, add a manual catalogue case in `checks.py`, for example a raw-SQL `both_columns_changed` database check.
7. **Evidence fixtures.** After tasks 3 and 4, regenerate `examples/` from fresh runs (`unsafe`, `staged` with v1 and v2, scripted loop) with v1 fields. Keep the live v0 fixture as is (do not rerun the 120B model for documentation).
8. **Held-out variation (only after integration checklist step 3 works).** Add one scenario variation, for example a different noncritical text field or a rename combined with a NOT NULL default. Keep its patches and expected outcomes under `heldout/`, excluded from every agent prompt and tool. Expose it through a CLI flag that Owner 2 can select without reading its contents.

## Bounty work: NeMo Gym environment (priority 3, after runner interfaces are stable)

Requirements are in `docs/bounties.md`, section 3.3. The track requirements are **unverified**, so ask the organizers first whether the bounty requires training or accepts an environment plus system or prompt improvement (question 4) before expanding scope.

1. **Prerequisites:** tasks 2-4 above (`runner_api.execute_plan`, v1 scope fields, start-state hashes), plus per-session resource allocation. Today `staged.py` uses fixed ports 8001 and 8002 and one database per call, so concurrent rollouts need allocated ports and `migproto_run_*` databases.
2. **Resources server in `gym/`.** Implement it with NeMo Gym's `SimpleResourcesServer`. `seed_session` creates the episode: a migration request (Review request contract), repository context (old-worktree search hits), a starting database state (a clone of `migproto_seed` with a recorded hash), and an execution budget (plan submissions, test runs, wall time).
3. **Tool endpoints** keyed by `session_id`, all calling existing code: `investigate` (wraps `agents/roles.gather_hits`), `submit_plan` (validates with `plans.py`), `run_tests` (calls `runner_api.execute_plan` and returns real observations with scope). There is no second execution implementation.
4. **`verify()` reward.** Use `BaseMultiRewardVerifyResponse` with components `plan_completed`, `mandatory_checks_passed` (the full manual catalogue, preservation, column agreement, retirement gate) and `data_preserved`. The scalar `reward` is 1.0 only if all three hold. Setup failures (Docker, Saleor start, seed) are returned as an explicit `setup_error` field and excluded from scores, never as reward 0 or 1.
5. **Anti-gaming.** No submitted plan, an invalid plan, a skipped contract, a disabled check, or a budget overrun scores 0. The agent cannot modify the catalogue, the gate, or verification code, and mandatory checks run regardless of what the agent asks. Add tests in `tests/gym/` for the do-nothing, skip-checks, and contract-before-retire episodes.
6. **Frozen tasks.** `gym/tasks/*.jsonl` holds the main scenario plus the held-out variation (task 8). Evaluate baseline and final systems on the same frozen file.
7. **Artifacts.** Save the config, trajectories, per-episode scores and `gym/README.md` reproduction instructions in `gym/results/<date>/`.
8. **Claim limits.** Evaluation integration shows a measured system or prompt improvement only. Describe any improvement as such unless weights were actually trained.

Additional owned paths: `gym/`, `tests/gym/`.

## Inputs and outputs

- Inputs: candidate plan and tester proposal JSON (from Owner 2), Execution request contract.
- Outputs: `runs/<run_id>/events.jsonl` and `summary.json` (v1), `runner_api.execute_plan` result, fixtures in `examples/`, contract updates.

## Dependencies

- Owner 2 consumes `runner_api` and feedback fields. Agree on the result shape before they wire it.
- Owner 3 consumes v1 events. Until then they use the v0 derivation rules.
- Owner 2's single-agent mode needs the same `runner_api` and the same mandatory checks. Nothing about verification may differ between topologies.

## Acceptance criteria

- `uv run pytest` passes, including new trigger regression tests over the three plan files.
- `uv run migproto all --skip-pytest` exits 0. The `unsafe` verdict is still `REPRODUCED`, scripted v1 still fails, and v2 still passes.
- Every `kind: check` line in a new run has non-null `run_id`, `attempt_id`, `stage_id`, `outcome`, and `schema_state` or an explicit `null` with a reason.
- `retirement_gate` refusals show `outcome: refused`, and SQL failures show `outcome: error`.
- Two attempts in one loop record identical start-state hashes.
- No change to the mandatory catalogue weakens an existing check.

Verification commands:

```bash
uv run pytest -q
uv run migproto unsafe
uv run migproto staged --plan proposals/scripted/plan_v1.json   # expect FAIL
uv run migproto staged --plan proposals/scripted/plan_v2.json   # expect PASS
uv run migproto staged --plan proposals/negative/plan_contract_before_retire.json --provenance manual  # expect refused gate
uv run migproto agent-loop --proposals scripted
```

## Out of scope

Agent prompts, model clients, orchestration logic and budgets (Owner 2). SQLite memory and UI (Owner 3). Rerunning the 120B model for documentation. Claims about concurrency safety or zero downtime.

## Suggested branch

`runner/verification-v1`

## Starting prompt for your coding LLM

```
You are working in the migproto repository root, role: Owner 1, runner and
verification. Read AGENTS.md, docs/contracts.md and docs/team/01-runner.md first. Work only
on the owned paths listed there. Do not edit migproto/agents/ or UI/memory code; propose such
changes in a handoff note.

Start with task 2 and task 3 of docs/team/01-runner.md: add migproto/runner_api.py with
execute_plan(request, progress=None) and add the additive v1 scope fields to check events,
keeping every existing v0 key and the CLI behaviour intact. Do not weaken or remove any
mandatory check. Do not describe the SQL guard as a sandbox.

After each change run: uv run pytest -q and uv run migproto agent-loop --proposals scripted.
Report the run_ids, the counts of checks by outcome, and any contract change you made to
docs/contracts.md.

Later (priority 3, only after runner_api and v1 fields are stable): the NeMo Gym section of
docs/team/01-runner.md. Wrap the existing runner as a resources server in gym/ without a second
execution path, score completion plus mandatory checks, report setup errors separately, and
make do-nothing or skipped-check episodes score 0. Confirm with organizers whether training is
required before expanding scope.
```
