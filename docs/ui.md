# How the UI works

The UI is a small local web app for looking at recorded migration runs. It lets you replay
past runs and look through their evidence, and it can start a new review.

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
you open it, so new batch results show up without a restart.

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

The list page shows, for each batch:

- its configuration note (`CONFIG-NOTE`);
- how many runs passed, failed, didn't finish (INCOMPLETE) or errored, for each scenario and
  agent setup ("roles" or "single").

Open a batch to see one row per run:

- **Verdict** and why the run stopped, if it stopped early.
- **Recommendation:** what the agent recommended next to what the runner concluded, and
  whether they agree.
- **Failed checks per attempt:** for example `12 → 11 → 8 → 9` means four attempts. Every
  attempt is shown, including failed ones. `+1⊘` means one action the runner refused, and
  `+1!` means one execution error; these are counted apart from failed checks. Hover over a
  number for the full breakdown.
- **Tokens, calls and wall time.**
- **Links:** "Replay" opens the run's pages above. "Trace ↗" opens its Langfuse trace, when
  one was recorded. The trace is for viewing only; the run's own files are the evidence.

**Infrastructure failures.** Sometimes a run fails because of our setup, for example the
network dropped, not because of the model. If a batch has an `INFRA-NOTE` naming those runs,
the UI lists them in a separate yellow section and leaves them out of the counts. The UI
finds them by the run timestamps written in the note (for example `20261017-192959`, or
`-193746` after a full one), so keep writing them that way.

**"Not recorded"** means the run didn't save that field; older runs, for example, have no
scenario or recommendation. The UI never fills in a guess.

The Batches page doesn't show expected answers or scores. Scoring is done separately.

## Run Review

Run Review starts one new review through the agent controller, which then runs the
migration test. The UI itself never runs SQL or starts Saleor. Only one review can run at a
time; if another is already running, the page says so. You can pick:

- **Proposals:** `scripted` (no model needed) or `llm` (uses the model set in the
  `MIGPROTO_LLM_*` environment variables);
- **Topology:** `roles` (separate investigator, planner and tester) or `single` (one agent).

The new run appears in `runs/` and its events show live on the page.

## Rules the UI follows

- Only the runner decides pass or fail. Agent statements are shown as claims, never as
  results.
- Failed attempts stay visible next to the final one.
- Missing information is shown as unknown, never guessed.
- A pass only covers what was tested: that data, those versions, that machine.
- Expected answers and reference solutions (`proposals/manual`, `proposals/scripted`,
  `patches/`) are never shown.

## For developers

| File | Role |
|---|---|
| `migproto/ui/server.py` | Web server: static files plus JSON at `/api/*` |
| `migproto/ui/static/` | The pages (`index.html`, `app.js`, `style.css`); no build step |
| `migproto/ui/review.py` | Run Review |
| `migproto/memory/adapter.py`, `store.py` | Load run folders into the evidence database |
| `migproto/memory/query.py` | Queries behind each page, including the Batches reader |

Tests: `uv run pytest -q tests/memory`. These need no Docker or Postgres.
