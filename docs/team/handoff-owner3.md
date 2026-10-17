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
- `migproto/ui/server.py` + `migproto/ui/static/` is the read-only "Replay Recorded Run" screen, built on stdlib `http.server` plus static HTML/JS with no build step. Run Review is disabled ("controller not available") and `POST` returns 503.
- `migproto/cli.py` gets a small `ui` subcommand (`--port`, `--db`). **Harsha: please review. This is the only change outside my owned paths.**
- `tests/memory/` adds 22 tests.
- No new dependencies.

## Verification run
```bash
uv run pytest -q tests/memory     # 22 passed (run here with Python 3.14 + pytest, since uv is not installed on this Windows machine)
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
