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
uv run pytest -q tests/memory        # 22 tests, under 1 s, no Docker needed
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
| `migproto/ui/server.py` | `serve()`, `make_handler(conn, lock)` | Stdlib `ThreadingHTTPServer`. Endpoints: `/api/runs`, `/api/run?id=`, `/api/matrix?run=&attempt=`, `/api/observation?id=`, `/api/history?check=`. `POST` returns 503 (Run Review not wired) |
| `migproto/ui/static/app.js` | `route()` plus one `page*` function per page | Hash routes: `#/`, `#/run/<id>`, `#/run/<id>/plan/<a>`, `#/run/<id>/matrix/<a>`, `#/run/<id>/agents`, `#/run/<id>/metrics`, `#/obs/<id>`, `#/compare/<a>/<b>`. No build step |
| `migproto/cli.py` | `cmd_ui` | The only edit outside Owner 3's paths (Harsha owns `cli.py`) |
| `tests/memory/` | `test_adapter.py` (10), `test_store.py` (8), `test_ui.py` (4) | Assert the fixture facts from the assignment's acceptance criteria |

## Concepts you need
- **IDs:**
  - `observation_id` = `<run_id>/<attempt>/<check name>#<line number>`
  - `attempt_id` = `<run_id>/a<N>`
  - for `examples/`, `run_id` is the folder name (e.g. `live-ordering-revision`)
- **`scope_source`:** `derived_v0` means scope was worked out from line order. `recorded_v1` means the runner wrote it explicitly (no v1 runs exist yet).
- **App version** comes only from each step's `app` field: `old`, or `new-staged` / `new-unsafe` depending on the command. Checks without `steps` (database-level checks, runner gates, the `unsafe` run's single-op checks) have no app version.
- **Schema state** is set only inside SQL stages (from the `schema` event). In `S0`, `deploy_new` and `retire_old` it is unknown.
- **`status` vs `outcome`:** `status` is the raw `PASS`/`FAIL`. `outcome` stays NULL for v0, because v0 `FAIL` can mean a failed assertion, a refusal or an execution error (contracts G2).
- **Matrix columns:** first step's app → last step's app. Cases that start and end on the same version but switch in between go in `mixed`.
- **Claims:** today they are free text from `agents/*.json` (planner `write_consistency` / `revision_notes`, tester `summary` / `suspected_cause`, and investigator findings with `needs_change: true`). They have no evidence links, so `claim_evidence` is empty.

## Gotchas
- **Delete `data/memory.sqlite` after changing the adapter or the schema.** Ingest is append-only (`INSERT OR IGNORE`), so rows already stored keep their old derived values. The tests use `:memory:` and aren't affected.
- **The server ingests once, at startup.** Restart `migproto ui` to pick up new `runs/`.
- **Windows console:** printing `→` fails with cp1252. That's why the matrix column labels use ASCII `->` and the UI swaps in `→` for display.
- **Fixture leak:** `query_observations` can return `examples/` outcomes. Evaluated agents must never see those, so any agent-facing use must pass `source_kind="local_run"`.
- **Python version:** code was tested with Python 3.14 locally. The project pins 3.12. It uses nothing newer than 3.10 syntax, but run the tests under `uv` once.

## Open items, in priority order
1. **Get the PR merged.**
   - Open a PR from `memory-ui/scoped-review`.
   - Harsha reviews the `cli.py` change and the decisions listed in `handoff-owner3.md`.
   - Someone on Linux runs `uv run pytest` and `uv run migproto agent-loop --proposals scripted` before merging.
2. **v1 fields (when Harsha ships them, G1–G3).**
   - `_from_v1` in `adapter.py` is written against the example in `docs/contracts.md` section 4. Check it against the first real v1 run.
   - Show `outcome` (`fail` / `refused` / `error`) in the matrix and on the Evidence page.
3. **Structured claims (when Owner 2 ships them, G7).**
   - Parse claims with `evidence[]` into `claims` / `claim_evidence`.
   - Add an `/api/claim?id=` endpoint around `query.evidence_for_claim`.
   - Link each claim to its observations on the Agents page.
4. **Scoped retrieval for agents (integration step 3).** Agree with Owner 2 on which query functions the controller calls. Make sure fixtures are excluded and only local runs are reachable.
5. **Run Review (blocked on G11, the controller and runner API).**
   - Wire the disabled button to the controller entry point only.
   - Show live progress by tailing `runs/<id>/events.jsonl`, or through the progress callback once it exists.
   - Never shell out from the UI.
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
