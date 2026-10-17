# Assignment 03: Scoped Memory and Review UI (Owner 3)

## Goal

Store runner observations and agent claims with their exact scope and provenance, make them queryable, and present one review screen that explains a migration review from requested change to final tested outcome.

## Current status (at `feasibility-v1`)

- Nothing exists yet for memory or UI. Evidence lives in `runs/<run_id>/events.jsonl`, `summary.json`, `plan-*.json`, `sql/*.sql` and `agents/*.json` (local, git-ignored), and in four frozen fixtures in `examples/`.
- Events are v0: scope must be derived with the official rules in `docs/contracts.md` ("v0 derivation rules"), and anything those rules do not cover is unknown.
- There is no controller API yet. Real execution is CLI-only (`uv run migproto agent-loop ...`). Harsha and Owner 2 are adding `runner_api.execute_plan` and a controller.

## Owned paths

New: `migproto/memory/` (adapter, store, query), `migproto/ui/` (server and static assets), `tests/memory/`, `docs/ui.md` (optional). Read-only use of `examples/` and `runs/`.

## Ordered tasks

1. **Adapter.** `migproto/memory/adapter.py` reads a run directory (`events.jsonl`, `summary.json`, `plan-*.json`, `sql/`, `agents/`) into normalized Test observation, Progress event, Agent claim and Final report records (`docs/contracts.md`). Apply only the v0 derivation rules and mark derived values `scope_source: derived_v0`. Leave everything else `null`. Pass v1 fields through unchanged when present.
2. **SQLite store.** `migproto/memory/store.py` uses stdlib `sqlite3`. Suggested tables: `runs`, `attempts`, `observations`, `claims`, `claim_evidence`, `plans`, `events`. Keep `provenance`, `definition_provenance` and `executed_by` as separate columns. Default DB path `data/memory.sqlite` (git-ignored by `*.sqlite`).
3. **Query API.** Exact-scope filtering (run, attempt, stage, app version, schema state, check name, outcome), evidence lookup for a claim, and history of a check across runs. Return outcomes with different scopes side by side and label them by scope. Never merge them or flag them as conflicts.
4. **Replay Recorded Run.** One review screen, labeled "Replay Recorded Run", for any directory in `examples/` or `runs/`, showing:
   - requested change
   - plan stages, with generated SQL collapsible
   - old/new compatibility matrix (rows are catalogue and tester cases, columns are writer and reader versions, cells are outcomes with links to step traces)
   - concise agent findings with their cited evidence
   - evidence details (step trace, schema state, errors)
   - the final tested outcome with all attempts, failed ones included

   Label fixtures as "recorded fixture" and show their `manifest.json` sanitization notes.
5. **Run Review.** Collect a review request and start it through the controller or runner interface only (`runner_api` / controller from Owners 1 and 2). While that interface does not exist, show the button disabled with "controller not available" and never shell out to SQL or servers from the UI. Show live progress by tailing `events.jsonl`, or via the progress callback once it exists.
6. **UI stack.** Choose a lightweight stack compatible with the repo: Python 3.12 managed by uv. Prefer stdlib `http.server` (or one small dependency added to `pyproject.toml` and noted in your handoff) serving JSON plus static HTML/JS with no build step. Add a CLI entry such as `migproto ui` through a small reviewed change to `cli.py`.

## Bounty work: evidence presentation (priority 2)

Context: `docs/bounties.md`. No bounty qualification is verified yet.

1. **Model and backend per run.** Show the actual model, checkpoint revision, serving backend and version, and hardware from each run's `agent_config` event. Show "not recorded" when a field is absent (v0 runs record only `model` and `base_url`). Label `gpt-oss:120b-cloud` runs "development run, outside submission budget".
2. **Results panel.** Compact, built from saved measurements only: task success (final verdict), verification failures (failed or refused observations per attempt), latency (per call and per run), and inference usage (tokens per call, totals from `agents/model_calls.json` or v1 summary fields).
3. **Gym results.** Show NeMo Gym evaluation scores from `gym/results/` once Owner 1 produces them. Until then, show the panel as "not connected".
4. **Recorded versus live.** Mark every view as either "Recorded run (replay)" or "Live run". Fixtures also show "sample fixture" and their manifest.
5. **No implied qualification.** Show no improvement that isn't computed from saved runs, and no sponsor logo or badge that implies qualification.

## Inputs and outputs

- Inputs: `examples/*/`, `runs/*/` (v0 now, v1 later), controller interface (later).
- Outputs: `data/memory.sqlite`, query functions used by Owner 2's scoped retrieval, the review UI.

## Dependencies

- Harsha: v1 scope fields, stable IDs, progress callback, contract decisions. Until then, use v0 derivation rules.
- Owner 2: Agent claim records with evidence IDs, controller entry point for Run Review. Until then, show free-text findings labeled "unstructured claim (v0)".

## Acceptance criteria

- Ingesting all four `examples/` fixtures reproduces their `summary.json` facts exactly:
  - `unsafe-rename`: `REPRODUCED`, 7 checks
  - `scripted-sync-failure`: `FAIL`, 29 checks, 9 failed
  - `scripted-staged-success`: `PASS`, 33 checks
  - `live-ordering-revision`: `PASS`, 71 checks, attempt 1 failed only `retirement_gate`, attempt 2 passed 41
- A check without a recorded or derivable scope field shows "unknown", never a guessed value. A test asserts this.
- The compatibility matrix for `scripted-sync-failure` shows old-writer/new-reader cells failing and new-writer/old-reader cells passing.
- The live fixture shows both attempts, the failed gate, and the revision notes.
- No path in the UI executes SQL, starts Saleor, or calls the model directly.

Verification commands:

```bash
uv run pytest -q tests/memory
uv run migproto ui   # or the entry point you add; open the printed URL and replay each examples/ fixture
```

## Out of scope

Semantic or vector search, additional memory agents, LongMemEval, changing runner or agent code, inferring missing scope, treating different-scope outcomes as conflicts, polishing beyond the one review screen.

## Suggested branch

`memory-ui/scoped-review`

## Starting prompt for your coding LLM

```
You are working in the migproto repository root, role: Owner 3, scoped memory
and UI. Read AGENTS.md, docs/contracts.md (especially "v0 derivation rules") and
docs/team/03-memory-ui.md first. Work only in new migproto/memory/, migproto/ui/ and
tests/memory/ (plus a small reviewed `ui` subcommand in cli.py). Do not modify runner or agent
modules.

Start with tasks 1-3: an adapter that ingests examples/*/ into SQLite using stdlib sqlite3,
preserving run/attempt/step/application/schema scope and provenance. Apply only the
documented v0 derivation rules, mark derived values scope_source=derived_v0, and leave
everything else null ("unknown"). Write tests that the four fixtures reproduce their
summary.json facts exactly.

Include the bounty evidence items from the assignment: show the actual model, revision,
serving backend and hardware per run ("not recorded" when absent), a compact results panel
from saved measurements only, and a clear Recorded-versus-Live label. Never imply bounty
qualification.

Then build the single "Replay Recorded Run" screen with a lightweight stack (stdlib
http.server + static HTML/JS preferred). Run Review must call the controller/runner
interface only; until it exists, show it disabled.
```
