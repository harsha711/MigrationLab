# Handoff: Owner 3 (scoped memory and review UI)

**Owner / branch:** Mahek (Owner 3) / `memory-ui/scoped-review`

## What changed (paths)
- `migproto/memory/adapter.py` reads a run directory (`events.jsonl`, `summary.json`, `plan-*.json`, `sql/`, `agents/`, `manifest.json`) and attaches scope using only the v0 derivation rules in `docs/contracts.md`. Derived values are marked `scope_source: derived_v0`, and anything else is `None`. Records with `contract_version: migproto.contracts/v1` pass their own fields through (`scope_source: recorded_v1`).
- `migproto/memory/store.py` is an append-only SQLite store at `data/memory.sqlite` (git-ignored by `*.sqlite`).
  - Tables: `runs`, `attempts`, `plans`, `stages`, `observations`, `observation_steps`, `claims`, `claim_evidence`, `events`.
  - Ingesting a run twice is a no-op.
  - `provenance` is split into `definition_provenance` and `executed_by`.
- `migproto/memory/query.py`:
  - `query_observations` filters on exact scope: run, attempt, plan, stage, check, status, app version, source kind.
  - Lookups: `get_observation`, `check_history` (one row per scope, never merged), `evidence_for_claim`, `run_detail`.
  - `matrix` builds the per-attempt old/new grid.
  - `results_panel` uses saved measurements only.
- `migproto/ui/server.py` + `migproto/ui/static/` is the read-only "Replay Recorded Run" UI, built on stdlib `http.server` plus static HTML/JS with no build step. Hash-routed pages:
  - Runs (`#/`)
  - per-run Overview, Plan, Matrix, Agents and Metrics tabs (`#/run/<id>/...`), with an attempt switcher
  - Evidence (`#/obs/<id>`)
  - Compare (`#/compare/<a>/<b>`, separate runs side by side, never merged)

  Run Review is disabled ("controller not available") and `POST` returns 503.
- `migproto/cli.py` gets a small `ui` subcommand (`--port`, `--db`). **Harsha: please review. This is the only change outside my owned paths.**
- `tests/memory/` adds 22 tests.
- No new dependencies.

## Verification run
```bash
uv run pytest -q tests/memory     # 48 passed (run here with Python 3.14 + pytest, since uv is not installed on this Windows machine)
uv run migproto ui                # http://127.0.0.1:8765/
```

**Fixture facts reproduced:**
- `unsafe-rename`: REPRODUCED, 7 checks
- `scripted-sync-failure`: FAIL, 29 checks, 9 failed
- `scripted-staged-success`: PASS, 33 checks
- `live-ordering-revision`: PASS, 71 checks. a1 has 30 checks and fails only `retirement_gate`; a2 has 41 checks, all passing. 8 model calls, 30,036 tokens.

**Matrix:** in `scripted-sync-failure` at `deploy_new`, every old→new case fails and every new→old case passes.

**Not run here:**
- `uv run pytest` for the runner tests (the trigger tests need local Postgres)
- `uv run migproto agent-loop --proposals scripted` (needs Linux and Docker)

Please run both before merging the `cli.py` change.

## v1 support (follow-up to `docs/team/handoff-01.md`)
The adapter reads the runner's implemented v1 shape from Harsha's `runner/verification-v1` branch, not the older proposal in `docs/contracts.md`.
- **Fields read:** `step_id` becomes the stage. Also `outcome`, `check_type`, `app_version` (stored as `app_scope`, so it isn't confused with per-step app versions), `app_commit`, `schema_state` (with `schema_hash`), `database_snapshot_id`, `expected` / `observed`, and `start_state` events.
- **IDs:** runs are keyed by directory name. That keeps exported fixtures (`examples/v1/<name>/`) consistent even though their recorded `run_id` differs. The recorded `run_id` / `attempt_id` stay in `raw`. The recorded `observation_id` is the primary key, so claims can cite it.
- **SKIP:** `status: "SKIP"` / `outcome: skipped` is never counted as a failure. `checks_failed` keeps its v0 meaning.
- **UI:**
  - The matrix and chips show ✓ pass, ✗ fail, ⊘ refused, ! error and – skipped.
  - The Overview shows outcome counts.
  - The Evidence page shows the v1 fields.
  - Metrics shows the snapshot id and start state.
- **Existing stores:** `data/memory.sqlite` gains the new columns through an additive `ALTER TABLE`. No rows are lost.
- **Tested against a synthetic fixture only:** `tests/memory/fixtures/v1-synthetic-staged/` is hand-written from the handoff-01 field list. No real v1 run is in git yet. **Harsha:** once `examples/v1/` exists, add one of those runs to the tests and delete the synthetic fixture.

**Merge note for Harsha:** on `runner/verification-v1`, `cli.py` imports `migproto.lock` at module level, and `lock.py` imports `fcntl`, which doesn't exist on Windows. After both branches merge, `migproto ui` (a read-only command) would crash on Windows at import time. Suggested fix: import `lock` inside `_run` / `cmd_all`, or give `lock.py` a non-`fcntl` fallback.

## Integration round (Owner 2 records, retrieval, Run Review)
This was coded against `origin/feature/aavritti/small-models` (Owner 2) and `origin/runner/verification-v1` (Harsha) without merging either branch. Every cross-owner import is lazy, so this branch runs on its own, with those features reported as unavailable.

**What this branch now does:**
- **Ingests Owner 2's output:**
  - `agent_claim` events become structured claims linked to their evidence. The recorded run-id prefix is rewritten to the directory name when that resolves the link. IDs that still don't match an observation are kept and marked `resolved=0`.
  - Planner `proposal` events give plan lineage (`parent_plan_id`, `solution_origin`).
  - `agents/review-request.json` gives the requested change.
  - `agent_config` gives topology, budget, sampling and serving fields. `summary.agent_totals` and `reason` are read, and `INCOMPLETE` passes through.
- **Skips unfinished runs:** run directories without `summary.json` are never ingested, so an in-progress run can't be frozen half-written into the append-only store.
- **`memory.retrieval.prior_evidence(change)`** is ready for the controller.
- **Run Review** (`migproto/ui/review.py`) is implemented. It has only been tested with a fake controller and a fake lock.

**For Owner 2:**
1. **Retrieval hook.** Suggested call in `run_review`, before `planner-1`:
   ```python
   from ..memory import retrieval
   prior = retrieval.prior_evidence(review["change"], model=llm.model,
                                    scenario=review["scenario"], exclude_run_id=log.run_id)
   log.write_json("agents/prior-evidence.json", prior)
   log.event("retrieval", observation_ids=[o["observation_id"] for o in prior["observations"]])
   # then pass prior["observations"] to roles.plan(...) through a new keyword argument
   ```
   - **Filters are hard-coded:** only finished local runs, only `agent:` provenance (so scripted `plan_v2` content never reaches a prompt), only provenance exactly `agent:<model>` for the calling model (so local stand-in runs never reach a Nemotron prompt), and only the same table, old column and new column. With `scenario`, only requests naming that scenario id. A run without a review request, or without a `scenario` field when one is asked for, is excluded.
   - **Prompt wording is yours.** The UI already shows `retrieval` events on the Agents page.
2. **`serving_metadata()` defaults `serving_backend` to `"vllm"`** even when nothing was configured. Runs on Ollama or a cloud model would then *record* vLLM. Please default it to `None`, so "not recorded" stays honest. The UI labels the field "as recorded".
3. **Evidence IDs match.** Your `<run_dir>/a<N>/<check>#<line>` matches the store's v0-derived IDs, and Harsha's v1 `observation_id` uses the same format.
4. **Summary:** Run Review writes the run summary with the same verdict rule as `cli._run`. A shared helper in the controller would avoid keeping the two in sync.

**For Harsha:**
1. **Lock:** Run Review takes `migproto.lock` (`acquire("ui-review-<mode>")` plus `update_owner(run_id=...)`) in its worker thread and calls `run_review` in that same thread, relying on re-entrancy when `execute_plan` takes the lock again. If another execution holds it, the UI answers 409 with the owner details.
2. **Windows:** the `fcntl` merge note above still applies. On Windows, Run Review reports "execution lock unavailable on this platform" instead of failing.
3. **Unfinished runs:** the store skips runs without `summary.json`. If a crashed run never writes one, it stays out of the store until someone writes an `ERROR` summary.

## Decisions to review
1. **Schema state:** applied to every check inside a SQL stage, from that stage's `schema` event (rule 4). It's `None` in `S0`, `deploy_new` and `retire_old`.
2. **`plan_id` on checks:** each check gets the plan of its attempt (`attempt` event `plan_id`, or `summary.plan_id` for standalone `staged`). The v0 rules don't name `plan_id` explicitly. Is this acceptable?
3. **Loop runs:** lines before the first `attempt` event (the investigator) get `attempt = None`. Investigator findings are stored as claims with an unknown attempt. Only `needs_change: true` findings are kept as claims.
4. **App version:** comes only from `steps[].app`. Checks without steps (including `unsafe`'s `old.read_fails` etc.) have no app version and appear under "checks without step-level app scope".
5. **Matrix columns:** first step's app → last step's app. Cases that start and end on one version but cross to the other in between go in a `mixed` column.
6. **`outcome`:** stays `None` for v0. The UI says v0 `FAIL` can mean an assertion failure, a refusal or an error (G2).

## Contract changes proposed
| Field | Producer | Consumer | Example |
|---|---|---|---|
| `run_id`, `attempt_id`, `stage_id` on every check (G1) | runner (Harsha) | memory adapter | `{"run_id": "20261017-123652-agent-loop-llm", "attempt_id": "…/a1", "stage_id": "deploy_new", …}` |
| `outcome` + `expectation` (G2) | runner | adapter, UI | `{"outcome": "refused", "expectation": "succeeds"}` |
| `schema` event for `S0`, `deploy_new`, `retire_old` (G3) | runner | adapter | `{"kind": "schema", "stage": "deploy_new", "note_columns": [...], "triggers": [...]}` |
| Investigator phase marker (e.g. `{"kind": "attempt", "name": "investigate"}` or `attempt_id: ".../a0"`) | controller (Owner 2) | adapter | lets investigator output have a known scope |
| Structured claims with `evidence[]` observation IDs (G7) | agents (Owner 2) | `claims` / `claim_evidence` tables | see `docs/contracts.md` §5 |
| `checkpoint_revision`, `serving_backend`, `hardware` in `agent_config` (G12) | Owner 2 | results panel | `{"kind": "agent_config", "model": "nemotron-3.5-lightning", "serving_backend": "vllm 0.27.1", "hardware": "1x H100 (Brev)"}` |
| Requested-change record (G6) | Owner 2 / UI | UI header | today the header shows "not recorded" except for `unsafe`'s `scenario` event |

## Known gaps / blockers for other owners
- **Owner 2, fixture leak risk:** `query_observations` can return `examples/` outcomes. Evaluated agents must not see them, so pass `source_kind="local_run"`, and keep the fixtures out of any store an agent can reach.
- **Run Review:** waits for `runner_api` / controller (G11). Live progress will tail `events.jsonl` once that exists.
- **NeMo Gym panel:** shows "not connected" until `gym/results/` and a score record shape (G13) exist.
- **Ingest timing:** runs are ingested once at server start. Restart `migproto ui` to pick up new `runs/`.
