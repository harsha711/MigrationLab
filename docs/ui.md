# How the UI works

The UI is a small local web app for looking at recorded migration runs. It lets you replay
past runs and look through their evidence, and it can start a new review.

The visual layout draws on CodePen examples for a [compact dashboard sidebar and card rhythm](https://codepen.io/Ahmod-Musa/pen/zxvOaBY) and a [responsive light and dark dashboard](https://codepen.io/whosajid/pen/dPPLddZ). The repository uses its own CSS and evidence-specific components; no example code or assets were copied.

## Start it

```bash
uv run migproto ui                      # http://127.0.0.1:8765/
uv run migproto ui --port 8766 --db /tmp/scratch.sqlite
```

- `--port`: the port to serve on. Use another one if 8765 is busy.
- `--db`: where the evidence database lives. The default is `data/memory.sqlite`. Use a
  scratch file if you don't want to touch the shared one.

No Docker, Postgres or model is needed to look at recorded runs.

## Where the data comes from

When the UI starts, it loads every finished run from `examples/` and `runs/` into the
evidence database. A run counts as finished once it has a `summary.json`. Runs that are
still in progress are skipped; restart the UI to pick them up later.

Loading is add-only. A run that is already in the database is never changed or overwritten.

The Batches page is the exception: it reads `runs/brev-batch-*` straight from disk each time
you open it (and every 20 s while you stay on the page), so a still-running batch's
progress shows without a restart. The UI never writes under `runs/`.

## Pages

Every page is read-only unless it says otherwise.

| Page | Address | What it shows |
|---|---|---|
| Migration evidence | `#/` | Home page: a summary of one selected run and a table of all runs. Tick two runs to compare them. |
| Run overview | `#/run/<run>` | The requested change and every attempt, failed ones included. |
| Plan | `#/run/<run>/plan/<attempt>` | The plan's stages and SQL, and which stages actually ran. |
| Matrix | `#/run/<run>/matrix/<attempt>` | Old app vs new app: which reads and writes worked across versions, stage by stage. |
| Agents | `#/run/<run>/agents` | What each agent role said. |
| Metrics | `#/run/<run>/metrics` | Model, endpoint, tokens, calls and timings. |
| Evidence | `#/obs/<id>` | One check in full: scope, steps, error, and the same check in other runs. Clicking any pass or fail mark opens this in a side panel. |
| Compare | `#/compare/<run>/<run>` | Two runs side by side. Their results are never combined. |
| Data checks | `#/workspace/<run>` | Guided view of one run: stages, compatibility table, agent handoffs, failed plan next to revised plan. |
| Guided demo | `#/demo/<run>` | Step-by-step replay of a recorded run. |
| Batches | `#/batches` | Brev test batches, explained below. |
| Run Review | `#/review` | Starts a new review. This is the only page that runs anything. |

## Batches

A batch is a group of agent runs launched together on Brev by `scripts/brev/supervise.py`.
Each batch has a folder `runs/brev-batch-<time>/`.

A batch is **finished** only when `supervisor.log` has a `DONE` line. Until then it is
**running**. Without a `supervisor.log` its status is unknown.

The list page shows, for each batch:

- running or finished, plus how many runs are in progress;
- its configuration note (`CONFIG-NOTE`);
- how many runs passed, failed, didn't finish (INCOMPLETE) or errored, for each scenario and
  agent setup ("roles" or "single");
- how many of those match the manual expected verdicts (see scoring below).

Open a batch to see:

- **Running now:** lines in `supervisor.log` of the form
  `RUN n/N <scenario> <topology> starting on worker W` that do not yet have a `runs.jsonl`
  row. Each shows elapsed time from the supervisor's clock. They are not counted as
  results.
- One row per finished run:
  - **Verdict** and why the run stopped, if it stopped early.
  - **Recommendation:** what the agent recommended next to what the runner concluded, and
    whether they agree.
  - **Failed checks per attempt:** for example `12 → 11 → 8 → 9` means four attempts. Every
    attempt is shown, including failed ones. `+1⊘` means one action the runner refused, and
    `+1!` means one execution error; these are counted apart from failed checks. Hover over a
    number for the full breakdown.
  - **Matches expected:** yes, no, or not scored. See scoring below.
  - **Tokens, calls and wall time.**
  - **Links:** "Replay" opens the run's pages above. "Trace ↗" opens its Langfuse trace, when
    one was recorded. The trace is for viewing only; the run's own files are the evidence.

**Scoring.** The column is labelled "scored against manual expected verdicts (never shown to
agents)". The rule is the same as `scripts/brev/score_scenarios.py`'s `target()` (copied into
`query.expected_target` until that script is on this branch): PASS with any listed PASS
recommendation when a safe plan exists in `proposals/manual/scenarios/<id>.json`, else
FAIL/reject. A run matches when the runner's verdict and `runner_recommendation` for the
final plan equal that target. Runs named in `INFRA-NOTE` are never scored. `rename-note`
has no expected file and is not scored. Scripted plans (`proposals/scripted`) are never
read. Agent-facing code (`memory.retrieval`, the controller) never calls this scoring.

**Infrastructure failures.** Sometimes a run fails because of our setup, for example the
network dropped, not because of the model. If a batch has an `INFRA-NOTE` naming those runs,
the UI lists them in a separate yellow section and leaves them out of the counts and the
score. The UI finds them by the run timestamps written in the note (for example
`20261017-192959`, or `-193746` after a full one), so keep writing them that way.

**Shared run directories.** Run IDs have one-second resolution. If two batches start a run
in the same second they share `runs/<run_id>/`, and the later `summary.json` replaces the
earlier. The UI then shows only the `runs.jsonl` row, flags both runs, does not score them,
and does not link Replay or Trace (those files may mix both runs).

**"Not recorded"** means the run didn't save that field; older runs, for example, have no
scenario or recommendation. The UI never fills in a guess.

Batches tonight may also live in `../hackathon-variant/runs/`. The UI has one root (`runs/`
next to the checkout). Copy finished batch directories into `runs/` rather than adding a
second root.

## Run Review

Run Review starts one new review through the agent controller, which then runs the
migration test. The UI itself never runs SQL or starts Saleor. Only one review can run at a
time; if another is already running, the page says so. You can pick:

- **Proposals:** `scripted` (no model needed) or `llm` (uses the model set in the
  `MIGPROTO_LLM_*` environment variables);
- **Topology:** `roles` (separate investigator, planner and tester) or `single` (one agent).

The new run appears in `runs/` and its events show live on the page.

**What has been exercised.** 2026-10-17, from `#/review` on this branch: Proposals
`scripted` (no model), topology `roles`, through `agents.controller.run_review` =
`agents.loop.run_review`, under a real lock file (`MIGPROTO_LOCK_PATH=.../migproto.lock`).
Saleor ran on ports 8091/8092 with Redis DB 9 so it would not collide with live batches.
Run `20261017-213756-agent-loop-scripted`: PASS in 65.2 s, 64 checks, attempt 1 had 9
failed checks and attempt 2 had 0 (both kept in `summary.json`). The run was ingested and
its Replay page loaded. `uv run pytest tests/memory/test_review_controller.py` covers the
same UI path (lazy import of the real controller and lock, flock on
`MIGPROTO_LOCK_PATH`) with only `loop._run_review` stubbed so the suite does not start
Saleor. LLM mode from the UI has not been exercised.

## Rules the UI follows

- Only the runner decides pass or fail. Agent statements are shown as claims, never as
  results.
- Failed attempts stay visible next to the final one.
- Missing information is shown as unknown, never guessed.
- A pass only covers what was tested: that data, those versions, that machine.
- Expected answers and reference solutions (`proposals/manual`, `proposals/scripted`,
  `patches/`) are never shown to agents. The Batches page scores finished runs against
  `proposals/manual/scenarios/` on the evaluation side only, and labels the column to say
  so. Scripted plans are never read.

## For developers

| File | Role |
|---|---|
| `migproto/ui/server.py` | Web server: static files plus JSON at `/api/*` |
| `migproto/ui/static/` | The pages (`index.html`, `app.js`, `style.css`); no build step |
| `migproto/ui/review.py` | Run Review |
| `migproto/memory/adapter.py`, `store.py` | Load run folders into the evidence database |
| `migproto/memory/query.py` | Queries behind each page, including the Batches reader |

Tests: `uv run pytest -q tests/memory`. These need no Docker or Postgres.
