# Handoff 01: runner verification v1 (in progress)

```
Owner / branch:   Owner 1 (Harsha) / runner/verification-v1 (local only, not pushed)
Status:           steps 1-3 of 6 done and committed; steps 4-6 not started
Paused:           2026-10-17 14:52 local, to free the laptop for a Nemotron model run
```

## Commits on the branch (on top of `main` at `0ddde0c`)

| Commit | What |
|---|---|
| `9eef139` | README: possible future migration enhancements |
| `5a456de` | v1 scoped observations |
| `015f08e` | shared execution lock |
| `6ddab95` | stable `execute_plan` API, used by CLI `staged` and the agent loop |

The working tree was clean at pause. No runner processes or Saleor servers were running and the lock was free.

## What changed (paths)

- New: `migproto/scope.py`, `migproto/lock.py`, `migproto/runner_api.py`, `tests/test_runlog_scope.py`, `tests/test_lock.py`, `tests/test_runner_api.py`.
- Changed (owned): `migproto/runlog.py`, `migproto/checks.py`, `migproto/staged.py`, `migproto/unsafe.py`, `migproto/baseline.py`, `migproto/cli.py`, `README.md`.
- Changed (Owner 2, minimal compatibility): `migproto/agents/loop.py`. The single `staged.run(...)` call became `runner_api.execute_plan(...).as_dict()`, and the import changed from `staged` to `runner_api`. Nothing else in that file changed.

## Stable runner API

```python
from migproto.runlog import RunLog
from migproto.runner_api import RunContext, execute_plan

log = RunLog("caller-name")                      # one per run, writes runs/<run_id>/
report = execute_plan(plan, run_context=RunContext(log, provenance="agent:<model>"),
                      extra_tests=tester_proposal)   # optional, same shape as before
```

- `RunContext(log, provenance="scripted", attempt=None, keep_db=False, progress=None)`. If `attempt` is `None`, the run log's current attempt is used. The attempt number goes up each time an `{"kind": "attempt"}` event is written. `progress` is called with every event dict as it is written. A failing callback is printed and ignored.
- `ExecutionReport` fields:
  - `verdict`: `pass`, `fail`, `refused` or `error`. When several outcomes occur, the order is `error` before `fail` before `refused`.
  - `ok`, `plan_id`, `run_id`, `attempt_id`, `halted`, `halted_step`, `completed_all_stages`, `stages`, `failed_checks`, `outcome_counts`.
  - `database`: `run_db`, `dropped`, `template_db`, `database_snapshot_id`, `final_schema_state`.
  - `artifacts`: paths relative to the run directory.
  - `observations`: this attempt's check records, all fields included.
- `report.as_dict()` puts the v0 result keys first and leaves out `observations` by default (`include_observations=True` adds them). It is ordered this way because `roles.report_evidence` cuts `result` off at 8000 characters.
- `failed_checks` now holds full `fail`, `refused` and `error` observations, minus repeated scope keys. The `retirement_gate` entry's `msg` and `old_alive` now reach the planner (contracts gap G5). The cost is longer feedback text.
- `staged.run` is internal. A test enforces that nothing except `runner_api.py` calls it.

## Lock usage

- `migproto.lock.execution_lock(command, run_id=None)` is a context manager. `acquire()` and `release()` are also available. It uses `flock` on `runs/.migproto-execution.lock`, which `MIGPROTO_LOCK_PATH` can override.
- When the lock is taken, the caller gets `lock.ExecutionBusy` immediately. Its message starts with "Another MigrationLab execution is active.", and `.owner` holds the pid, host, command, `run_id`, argv and `started_at`.
- It is re-entrant in the same thread: `all` takes the lock and then the nested commands and `execute_plan` take it again. A different thread in the same process is refused. A future UI must run one execution at a time.
- The kernel releases the lock when the holder exits or crashes. A leftover lock file with a dead pid's metadata is ignored and overwritten.
- The CLI takes the lock in `cli._run`, before the `RunLog` is created, so a refused command creates no run directory and exits 2. `execute_plan` takes it as well. `scripts/export_example.py` and other commands that only read saved evidence never take it.
- Limits: local filesystems only. It does not detect processes outside MigrationLab that hold ports 8001 or 8002 (`AppServer.start` still refuses a busy port).

## New fields (additive, every v0 key kept)

Every event gets `contract_version` (`migproto.contracts/v1`), `run_id`, `attempt_id` (`<run_id>/a<N>`), `step_id` and `timestamp` (UTC ISO 8601). The relative `t` stays.

Check events also get:

| Field | Meaning |
|---|---|
| `observation_id` | `<attempt_id>/<name>#<seq>`. `seq` is the 0-based line index in `events.jsonl`, unique within a run |
| `outcome` | `pass`, `fail`, `refused`, `error` or `skipped`. The v0 `status` stays `PASS` or `FAIL` (`SKIP` for skipped) |
| `check_type` | `assertion`, `safety_gate` (`plan.schema_valid`, `retirement_gate`, `stage.*.preconditions`) or `execution` (`stage.*.executes`, `*.django_migrate`, `migration_succeeds`, `start_state.equivalent`) |
| `app_version` | `old`, `new`, `both`, `database` or `null`, set explicitly by the runner. For cases it comes from the apps in the case's own steps, so one-app smoke checks are never `both` |
| `app_commit` | `{"old": sha}`, `{"new": sha}` or both, limited to the apps involved. `null` for `database` and `null`. The new worktree's sha is machine-specific (recreated by `git am`). Pair it with `patch_sha256` from the `start_state` event |
| `schema_state` | `{table, note_columns, triggers, account_migration, schema_hash}`, introspected at `S0`, after every SQL stage, after the `deploy_new` migrate and after `retire_old`. `schema_hash` covers columns plus trigger definitions and function sources, not the migration name. `null` before a database exists |
| `database_snapshot_id` | `seed:<hash>` of the note values plus the schema hash, taken right after cloning the template |
| `expected`, `observed` | structured expectation and observation for each check type. In `unsafe`, `old.read_fails` now carries `expected: {"ok": false}` |
| `executed_by` | always `runner` |

Step ids: `validate`, `S0`, each plan stage id, `post_contract`. `unsafe` uses `S0`, `migrate_unsafe` and `post_migration`. `baseline` uses `seed`, `smoke_old` and `pytest_<version>`.

New per-attempt items:
- A `start_state` event: template, run database, snapshot id, schema state, and app identity (worktree, commit, pinned commit, patch SHA-256).
- A `start_state.equivalent` check. It records `outcome: error` and halts if an attempt starts from a different snapshot than an earlier attempt in the same run.
- After a halt, each stage that did not run gets a `stage.<id>.not_run` observation with `outcome: skipped`.

`summary.json` adds `contract_version`, `run_id` and `outcome_counts` for each attempt. The CLI `staged` summary also adds `runner_verdict`. `checks_total`, `checks_failed` (v0 status `FAIL`) and `verdict` keep their v0 meaning.

## Verification so far

- `uv run pytest`: 68 passed. The original 34 plus scope, lock and API tests.
- Run ids, local and git-ignored:
  - `20261017-142206-staged`: plan v2 `PASS`, 34 of 34 observations `pass`. It has one more check than v0 (`start_state.equivalent`). Observation ids are unique, and the schema hash changes at expand and contract.
  - `20261017-142254-agent-loop-scripted` and `20261017-142601-agent-loop-scripted` (the latter through `execute_plan`): `PASS`. Attempt 1 had 21 pass, 8 fail and 1 refused (`stage.contract.preconditions`). Attempt 2 had 34 pass. Both attempts had snapshot `seed:61e4e7a7f3caa544`.
  - `20261017-142439-staged`: `PASS`. A second command started while this run was active was refused in 0.18 s with exit 2 and the owner details, and no run directory was created.
- `unsafe`, plan v1 `staged` and the negative plan have **not** been run since the change.

## Remaining work (steps 4-6 of the plan)

4. **Trigger hardening.** Add `checks.probe_sync_triggers(dbname)`. In one transaction that is always rolled back, it renames the user triggers on `account_user` into every possible firing order (at most 3 triggers, 6 orders), then runs a conflicting update and an equal-values update on one row with `lock_timeout` set. Add the mandatory checks `<deploy_new>.trigger_conflict_deterministic` and `<deploy_new>.trigger_equal_values` (database-level assertions), run at `deploy_new` before the migrate.
   - Agreed policy: a result passes if it is the same under every trigger order and leaves the columns equal, or if it rejects the statement.
   - Expected effect: the live attempt-2 plan (two triggers) fails, scripted v2 passes, scripted v1 still fails.
   - Per-plan regression tables go in `tests/test_trigger_semantics.py`. They cover scripted v1, scripted v2, the recorded live plan, the live plan with its trigger names swapped, and a plan that rejects conflicting updates.
   - Analysis so far: the live design fires `trg_sync_customer_note_to_note` first, alphabetically. A conflicting update gives `customer_note` with the recorded names and `note` with the names swapped. The single-writer and insert paths look correct.
5. **Fixtures.** Fresh `unsafe`, plan v1, plan v2 and negative-plan runs, plus a replay of the two recorded live plans and tester proposals through `staged --provenance agent:gpt-oss:120b-cloud`. That is new runner observations, not a new model run. Export them into `examples/v1/<name>/`. The exporter should read `contract_version` from the run. The v0 fixtures stay where they are.
6. **Documentation.** `docs/contracts.md` (implemented v1 shape with real JSON, API, lock, conflict policy, gap-register updates), this file (final version), and minimal README, AGENTS and REPORT updates.

## Resuming

Postgres runs on tmpfs, and Docker was stopped at pause, so `migproto_seed` is gone.

```bash
git switch runner/verification-v1
uv run migproto setup                      # starts Docker Desktop and services if needed
uv run migproto baseline --skip-pytest     # rebuilds the seed (about 2.5 min)
uv run pytest && uv run migproto agent-loop --proposals scripted
```

A rebuilt seed gets a new `database_snapshot_id`, because `populatedb` data is random. Snapshot ids are only comparable within one seed build.

## For Owners 2 and 3

- **Owner 2:**
  - Call `runner_api.execute_plan`, not `staged.run`.
  - Expect larger `failed_checks` entries in planner feedback. They now include `outcome`, `check_type`, `expected`, `observed`, `msg` and `old_alive`.
  - Catch `lock.ExecutionBusy`.
  - Do not record observations from `migproto/agents/`. A test fails if `.check(` or `.skip(` appears there.
- **Owner 3:**
  - New runs carry v1 fields directly, so v0 derivation is only needed for the `examples/` fixtures.
  - Treat a missing `contract_version` as v0.
  - `status: "SKIP"` is a new v0-compatible value. Do not count it as a failure.
  - Use `outcome` to tell assertion failures, refusals and execution errors apart.

## Known gaps

- `docs/contracts.md` still describes v1 as proposed, with the old example shape (`stage_id`, `expectation`, `app_versions_live`). Step 6 updates it. Until then this file is authoritative.
- Not yet exercised on v1: `unsafe`, plan v1 `staged`, the negative plan and `baseline`.
- The trigger conflict check is not implemented, so the order-dependent live design would still pass today.
- Fixed ports 8001 and 8002 mean one execution per machine. The lock enforces this. Per-session ports for Gym are not started.
