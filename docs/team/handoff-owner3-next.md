# Continuing the scoped memory and Replay UI work (Owner 3)

This guide is for the teammate taking over Owner 3's area. It covers what exists, how to run it, how the code is laid out, and what is left to do.
- **Reviewer-facing note** (PR checklist, contract proposals): [handoff-owner3.md](handoff-owner3.md)
- **Original assignment:** [03-memory-ui.md](03-memory-ui.md)

**Branch:** `memory-ui/scoped-review` (pushed, not yet merged into `main`)

## Read first (30 minutes)
1. `AGENTS.md`: evidence rules 1–8 especially (only the runner creates checks, missing scope means unknown, never hide failed attempts).
2. `docs/contracts.md`: "v0 derivation rules" and sections 4 (Test observation) and 5 (Agent claim).
3. `examples/scripted-sync-failure/events.jsonl`: read it top to bottom. `stage` lines divide the timeline, and `check` lines are the evidence. Then `examples/live-ordering-revision/events.jsonl`, which has two `attempt` lines.

## What this area does
The runner writes each run to `runs/<id>/` (frozen copies live in `examples/<id>/`). Those files don't say which stage or attempt a check belongs to, so this code:
1. **Adapter:** reads a run folder and stamps scope (run, attempt, plan, stage, schema state, app version) onto every check, using only the v0 rules.
2. **Store:** saves the result in SQLite, append-only.
3. **Queries:** answer exact-scope questions for the UI, and later for Owner 2's agents.
4. **UI:** a read-only web page for replaying a run.

```
runs/<id>/, examples/<id>/  ->  adapter.py  ->  store.py (data/memory.sqlite)  ->  query.py  ->  ui/server.py -> browser
```

Nothing here runs SQL against Saleor, starts the apps, or calls a model. `tests/memory/test_ui.py` enforces that by checking imports.

## Run it
```bash
uv sync
uv run pytest -q tests/memory        # 48 tests, about 3 s, no Docker needed
uv run migproto ui                   # http://127.0.0.1:8765/  (--port, --db)
```
- **No `uv`?** `python -m migproto ui` also works. The memory and UI code is stdlib only. The tests need `pytest`.
- **Platform:** everything here runs on Windows, macOS or Linux, because it only reads files. The runner itself (`setup`, `staged`, `agent-loop`) needs Linux and Docker.

## Code map
| File | Key functions | Notes |
|---|---|---|
| `migproto/memory/adapter.py` | `load(run_dir)` (entry point), `read_run`, `derive`, `plans_by_attempt`, `claims`, `run_meta` | Pure functions, no DB. `derive` walks `events.jsonl` once, tracking the current attempt and stage. v1 records go through `_from_v1` |
| `migproto/memory/store.py` | `connect(path)`, `ingest(conn, run_dir)`, `ingest_all(conn)`, `run_dirs()` | Tables: `runs`, `attempts`, `plans`, `stages`, `observations`, `observation_steps`, `claims`, `claim_evidence`, `events`. `INSERT OR IGNORE` on primary keys |
| `migproto/memory/query.py` | `query_observations(**filters)`, `get_observation`, `check_history`, `evidence_for_claim`, `list_runs`, `run_detail`, `matrix`, `results_panel` | Filters are exact. `None` means "don't filter". Unknown (NULL) never matches a concrete value |
| `migproto/memory/retrieval.py` | `prior_evidence(change, db=, runs_root=, exclude_run_id=, limit=)` | For Owner 2's controller. Hard filters: only finished local runs (never `examples/`), only `agent:` provenance (no scripted `plan_v2`), only the same table, old column and new column (no request means unknown, excluded). Returns `excluded` counts for audit |
| `migproto/ui/server.py` | `serve()`, `make_handler(conn, lock, review)` | Stdlib `ThreadingHTTPServer`. Replay endpoints: `/api/runs`, `/api/run?id=`, `/api/matrix?run=&attempt=`, `/api/observation?id=`, `/api/history?check=`. Review endpoints: `GET /api/review/status`, `POST /api/review`, `GET /api/review/events?run=&after=`. POST bodies are always read before replying (Windows resets otherwise) |
| `migproto/ui/review.py` | `ReviewManager.available()`, `start(mode, topology)`, `events(run_id, after)` | Imports `migproto.agents.controller`, `migproto.lock` and `migproto.runlog` lazily (a test enforces this allowlist). One review at a time. Writes the summary with `cli._run`'s verdict rule, then ingests, then marks the review done |
| `migproto/ui/static/app.js` | `route()` plus one `page*` function per page | Hash routes: `#/`, `#/run/<id>`, `#/run/<id>/plan/<a>`, `#/run/<id>/matrix/<a>`, `#/run/<id>/agents`, `#/run/<id>/metrics`, `#/obs/<id>`, `#/compare/<a>/<b>`, `#/review`, `#/review/<run_id>`. No build step |
| `migproto/cli.py` | `cmd_ui` | The only edit outside Owner 3's paths (Harsha owns `cli.py`) |
| `tests/memory/` | `test_adapter.py` (10), `test_store.py` (8), `test_ui.py` (6), `test_v1.py` (6), `test_owner2.py` (7), `test_retrieval.py` (4), `test_review.py` (7); `review_fakes.py` | Assert the fixture facts from the assignment's acceptance criteria. `fixtures/v1-synthetic-staged` and `fixtures/owner2-synthetic-loop` are SYNTHETIC (`fixtures/make_owner2_synthetic_loop.py` regenerates the loop fixture). Run Review tests use fakes |

## Concepts you need
- **IDs:**
  - `observation_id` = `<run_id>/<attempt>/<check name>#<line number>`
  - `attempt_id` = `<run_id>/a<N>`
  - for `examples/`, `run_id` is the folder name (e.g. `live-ordering-revision`)
- **`scope_source`:** `derived_v0` means scope was worked out from line order. `recorded_v1` means the runner wrote it explicitly. The v1 shape is defined in `docs/team/handoff-01.md` on Harsha's `runner/verification-v1` branch.
- **v1 fields:** `outcome` (pass / fail / refused / error / skipped), `check_type`, `app_scope` (the runner's `app_version`: old / new / both / database), `app_commit`, `database_snapshot_id`, `expected` / `observed`. They are all NULL for v0 runs.
- **App version** comes only from each step's `app` field: `old`, or `new-staged` / `new-unsafe` depending on the command. Checks without `steps` (database-level checks, runner gates, the `unsafe` run's single-op checks) have no app version.
- **Schema state** is set only inside SQL stages (from the `schema` event). In `S0`, `deploy_new` and `retire_old` it is unknown.
- **`status` vs `outcome`:** `status` is the raw `PASS`/`FAIL`. `outcome` stays NULL for v0, because v0 `FAIL` can mean a failed assertion, a refusal or an execution error (contracts G2).
- **Matrix columns:** first step's app → last step's app. Cases that start and end on the same version but switch in between go in `mixed`.
- **Claims:** today they are free text from `agents/*.json` (planner `write_consistency` / `revision_notes`, tester `summary` / `suspected_cause`, and investigator findings with `needs_change: true`). They have no evidence links, so `claim_evidence` is empty.

## Gotchas
- **Delete `data/memory.sqlite` after changing how the adapter derives values.** Ingest is append-only (`INSERT OR IGNORE`), so rows already stored keep their old derived values.
  - New columns are different: add them to `SCHEMA` *and* to `store.ADDED_COLUMNS`, and existing stores gain them automatically.
  - The tests use `:memory:` and aren't affected.
- **The server ingests once, at startup.** Restart `migproto ui` to pick up new `runs/`.
- **Windows console:** printing `→` fails with cp1252. That's why the matrix column labels use ASCII `->` and the UI swaps in `→` for display.
- **Fixture leak:** `query_observations` can return `examples/` outcomes. Evaluated agents must never see those, so any agent-facing use must pass `source_kind="local_run"`.
- **Python version:** code was tested with Python 3.14 locally. The project pins 3.12. It uses nothing newer than 3.10 syntax, but run the tests under `uv` once.

## Open items, in priority order
1. **Get the PR merged.**
   - Open a PR from `memory-ui/scoped-review`.
   - Harsha reviews the `cli.py` change and the decisions listed in `handoff-owner3.md`.
   - Someone on Linux runs `uv run pytest` and `uv run migproto agent-loop --proposals scripted` before merging.
2. **Confirm v1 against real data.** v1 support is implemented and shown in the UI, but it has only been tested against the synthetic fixture `tests/memory/fixtures/v1-synthetic-staged/`. When Harsha exports `examples/v1/` (their step 5):
   - Ingest those runs.
   - Copy `test_v1.py`'s assertions over to a real run.
   - Delete the synthetic fixture.
   - Check one loop run in particular. Harsha's `RunLog` starts at attempt 1, so investigator events before the first `attempt` event carry `.../a1`. Investigator free-text claims are still stored with attempt unknown, and that should be confirmed on real data.
3. **Confirm Owner 2's records against a real run.** Structured claims, plan lineage, the review request and budgets are ingested and shown, but only tested against `fixtures/owner2-synthetic-loop`. Once a real `agent-loop` run from Owner 2's controller exists, ingest it, compare it with the fixture, then replace the fixture.
4. **Retrieval: Owner 2 wires the call.** `memory.retrieval.prior_evidence` is ready, and the suggested hook for `run_review` is in `handoff-owner3.md`.
   - Once it's wired, the Agents page shows "Prior evidence given to the planner" from `retrieval` events.
   - Whether retrieval helps planning is unmeasured. That belongs in Owner 2's single vs multi-agent evaluation.
   - It does not filter by model yet. On the local runs it selected only qwen3:4b stand-in runs, which would then reach Nemotron prompts. Add a same-model filter (from the run's `agent:<model>` provenance) before wiring the call.
5. **Run Review: test it for real.** It is implemented, but only tested with fakes. After both branches merge, on Linux with Docker (pair with Harsha):
   - Start a scripted review from `#/review`.
   - Confirm the live events, the lock refusal while a CLI run is active, and ingestion when the review finishes.
   - On Windows it stays unavailable (`fcntl`).
6. **NeMo Gym panel (blocked on G13).** Read `gym/results/` once Harsha defines the score record. Until then it says "not connected".
7. **Small improvements:**
   - add a "re-ingest" action instead of restarting the server
   - show a Lightning/vLLM run's `checkpoint_revision` / `serving_backend` / `hardware` once `agent_config` records them (G12; the Metrics page already shows "not recorded")

## Out of scope (from the assignment)
- semantic or vector search
- memory agents
- inferring missing scope
- treating different-scope outcomes as conflicts
- changing runner or agent code (propose changes to Harsha / Owner 2 instead)
