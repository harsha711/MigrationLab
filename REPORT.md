# Migration Reviewer Feasibility Prototype: Saleor `User.note` Rename

> Links to `runs/...` point to local, git-ignored run directories on the machine that produced them. Sanitized copies of the key runs are in [`examples/`](examples/README.md): `unsafe-rename` (from `runs/20261017-123935-unsafe`), `scripted-sync-failure` (`runs/20261017-123256-staged`), `scripted-staged-success` (`runs/20261017-123138-staged`), and `live-ordering-revision` (`runs/20261017-123652-agent-loop-llm`).

## 1. Outcome

The feasibility gate is met for one field and one rename pattern. An immediate column rename migrated successfully and the new application worked, while every old-application operation that loads a `User` row failed with `column account_user.note does not exist`. A staged expand, sync, backfill, retire, contract sequence passed all 33 deterministic checks with no data loss. In both the scripted run and one live model run, the agent loop revised its plan once after execution feedback and the revised plan passed. All pass and fail decisions came from Python code, not from the model.

**Scenario label.** The rename of `account_user.note` to `customer_note` is an intentionally introduced experiment. It is not an upstream Saleor bug.

## 2. Setup

| Item | Value |
|---|---|
| Target | `saleor/saleor` at `0c0e2697cc72aa999f7786ae8aeda7665bd4285f` (3.24.0-a.0) |
| Instructions followed | `AGENTS.md` and `CONTRIBUTING.md`: Python 3.12 with `uv sync --locked`, Postgres 15, pytest with `--reuse-db` and explicit paths |
| Database | `postgres:15-alpine` on tmpfs at `127.0.0.1:55432`, plus Valkey, through [`docker-compose.yml`](docker-compose.yml) |
| App versions | Three git worktrees under `vendor/`: `wt-old` (pinned, unmodified), `wt-new-unsafe`, and `wt-new-staged`, rebuilt from [`patches/`](patches) with `git am` |
| Data | `populatedb --createsuperuser --withoutimages` plus 200 synthetic customers with deterministic notes (NULL, empty, Unicode, quotes, newlines, a 2000-character value). 245 users in total, 191 with a non-NULL note |
| Isolation | Every scenario runs on a fresh `migproto_run_*` clone of the `migproto_seed` template, which is dropped afterwards |
| Model | Ollama `gpt-oss:120b-cloud` at `http://localhost:11434/v1`, configurable through `MIGPROTO_LLM_BASE_URL`, `MIGPROTO_LLM_MODEL`, and `MIGPROTO_LLM_API_KEY`. The model is cloud-backed, so prompts containing public Saleor code and synthetic logs left the machine |

### 2.1 Selected field and manual code change

`User.note` is a nullable `TextField` with no role in pricing, authentication, or order flow. It is read through the permission-gated GraphQL field `User.note` and written through `customerCreate`, `customerUpdate`, and `customerBulkUpdate`. The new application renames the model attribute to `customer_note` and keeps the GraphQL name `note`. The patch touches 7 files. One edit is not visible from a text search: `ModelMutation.construct_instance` copies input keys onto model fields by name, so without an explicit mapping in `clean_input` the GraphQL input `note` would be dropped silently instead of raising an error.

The two new versions share that code and differ only in migration `0107`. In `new-unsafe` it is Django's generated `RenameField`. In `new-staged` it is a state-only `SeparateDatabaseAndState`, and the staged plan supplies all schema SQL.

## 3. Commands

```bash
uv run migproto setup                         # services, pinned clone, worktrees from patches, venv
uv run migproto baseline [--rebuild-seed]     # seed template DB, smoke old app, pytest subset
uv run migproto unsafe                        # reproduce the old-app breakage
uv run migproto staged --plan proposals/scripted/plan_v2.json
uv run migproto agent-loop --proposals scripted|llm
uv run migproto all                           # setup + baseline + unsafe + scripted loop
uv run pytest                                 # 34 runner tests (guard, schema, trigger semantics)
```

Each invocation writes `runs/<timestamp>-<command>/` with `events.jsonl` (every event tagged `manual`, `scripted`, `runner`, or `agent:<model>`), `sql/`, server and migration logs, pytest output, model transcripts, and `summary.json`.

## 4. Results

### 4.1 Baseline

The old application passed four smoke checks (read, write, create, and the database column value) on [`runs/20261017-123847-baseline`](runs/20261017-123847-baseline). The existing test subset (`test_customer_create.py`, `test_customer_update.py`, `test_me.py`, `test_customer_bulk_update.py`) passed 110 of 110 tests on both `old` and `new-unsafe`, so the renamed code keeps the GraphQL contract. I did not run pytest on `new-staged`, because its state-only migration leaves the test database column named `note` while the model expects `customer_note`. That version is only correct together with the plan's SQL.

### 4.2 Unsafe immediate rename (failed as intended)

Evidence: [`runs/20261017-123935-unsafe`](runs/20261017-123935-unsafe) (`evidence.json`, `sql/unsafe_0107.sql`, `server-old-tail.log`, `reproduce.sh`).

```sql
ALTER TABLE "account_user" RENAME COLUMN "note" TO "customer_note";
```

| Step | Result |
|---|---|
| `manage.py migrate account 0107` (new-unsafe) | exit 0 |
| New app `customerUpdate(input:{note})` then `user(id){note}` | succeeded, value round-trips |
| Old app `query user(id){note}` | `ProgrammingError: column account_user.note does not exist` |
| Old app `mutation customerUpdate(input:{note})` | same error |
| Old app `customerCreate` | same error |
| Old server log | `psycopg.errors.UndefinedColumn: column account_user.note does not exist` |
| Old app anonymous `{ shop { name } }` | succeeded |

The failure is wider than the renamed field. The old Django model selects every column it knows, so any old-application request that loads a `User` row fails, including token authentication. Requests that never load a `User` row kept working. This matters for review because a test that only exercises `note` would understate the blast radius.

### 4.3 Staged alternative (passed)

Plan v2 ([`proposals/scripted/plan_v2.json`](proposals/scripted/plan_v2.json)) runs in this order:

1. Add the nullable `customer_note` column.
2. Install a bidirectional `BEFORE INSERT OR UPDATE` trigger.
3. Backfill in batches of 100 until no row differs.
4. Deploy the new app next to the old one.
5. Stop the old app and verify its port is closed.
6. Drop the trigger, the function, and `note`.

Old writers never mention `customer_note` and new writers never mention `note`, so consistency is enforced in the database rather than in either application. On UPDATE, a change to only `note` is copied to `customer_note`, and a change to `customer_note` is copied back. On INSERT, whichever column is NULL is filled from the other.

The runner checked application behaviour and data at every stage:
- **Data preservation:** every row was compared against an expected-value map that the runner updates after each application write.
- **Column agreement:** the count of rows where `note IS DISTINCT FROM customer_note` must be zero.
- **Interleaved operations, with both versions live:** 8 mandatory catalogue cases plus 2 tester-proposed cases. They cover writes, creates, and clears in each direction, alternating updates to the same row, and Unicode.

All 33 checks passed in [`runs/20261017-123138-staged`](runs/20261017-123138-staged). After the contract stage, a briefly restarted old app failed as expected, which is why the retirement gate exists. A negative fixture that orders the contract stage before `retire_old` was refused by the gate (`contract stage contract refused: old app still running`, [`runs/20261017-123332-staged`](runs/20261017-123332-staged)).

### 4.4 Dual-write in the new application does not cover old writers

The deliberately flawed scripted plan v1 syncs only when `customer_note` changes, which mimics the new application dual-writing on its own writes. The checks where the new app writes and the old app reads passed. Old writes, creates, and clears after the backfill never reached `customer_note`. The new app read stale values such as `read 'seed#194: ...', expected 'old wrote this'`, 7 rows disagreed between the columns, and the runner skipped the contract stage because earlier checks had failed. The SQL-level test `test_v1_misses_old_writer` reproduces the same behaviour without the applications.

### 4.5 Agent loop

| Run | Attempt 1 | Feedback used | Attempt 2 | Evidence |
|---|---|---|---|---|
| Scripted | v1 failed 9 checks (old writes not synced) | failing check IDs, steps, values | v2 passed | [`runs/20261017-123957-agent-loop-scripted`](runs/20261017-123957-agent-loop-scripted) |
| Live `gpt-oss:120b-cloud` | two-trigger sync passed every interleaved and preservation check, but put the contract stage before `retire_old`, so `retirement_gate` failed | gate failure and halt reason | contract moved after `retire_old`, passed | [`runs/20261017-123652-agent-loop-llm`](runs/20261017-123652-agent-loop-llm) |

The live run took 88 seconds. Its revision notes attributed the failure to the stage ordering, which matches the runner's evidence. The model did not fall into the dual-write trap from section 4.4, so the live run exercised revision on a different defect from the scripted run. The live investigator classified 35 search hits with precision 1.0 and recall 0.875 against the manual ground truth. It missed the specific line `saleor/graphql/account/mutations/base.py:182` but listed the underlying mechanism among its implicit paths (generic code that sets model attributes from input keys).

In the scripted loop's `summary.json`, `checks_failed` includes the failures from attempt 1. The verdict reflects the final attempt.

## 5. Provenance

| Step | Source |
|---|---|
| Choice of target commit and field, the app patches, the unsafe and state-only migrations | manual |
| Check catalogue, expectation logic, retirement gate, safety guard, investigator ground truth | manual (runner code) |
| Scripted investigator output, plan v1 (deliberately flawed), plan v2, tester cases | scripted, manually authored |
| `note` search over the old worktree | runner tool, deterministic |
| Live run: hit classification, both plans and their SQL, 5 tester cases, evidence summaries, revision notes | agent (`gpt-oss:120b-cloud`) |
| Every pass and fail decision, SQL execution, server lifecycle | runner (deterministic Python) |

## 6. Setup effort

Implementation took about 45 minutes of wall-clock time after about 10 minutes of investigation, well inside the 2-hour time box. There were no hard blockers. The soft ones were a stopped Docker Desktop service, which needed `systemctl --user start docker-desktop`, and system Python 3.10, which needed the `uv`-managed 3.12.9.

| Step | Time |
|---|---|
| Shallow clone at the pinned commit | 4 s |
| `uv sync --locked` (shared venv) | 31 s |
| Docker Desktop start and services healthy | about 20 s |
| `migrate` on an empty database | 113 s |
| `populatedb` plus synthetic customers | 29 s |
| pytest subset, first run per version (creates the test DB) | 123 s and 128 s |
| pytest subset, reused DB | 24 s and 19 s |
| `unsafe` / `staged` / live agent loop | 22 s / 31 s / 88 s |

Three practical findings affected the runner:
- Each Saleor process generates its own temporary RSA key, so JWTs are per server and expire after 5 minutes.
- The generic input-to-model mapping drops renamed fields silently instead of raising an error.
- A state-only migration cannot be tested in isolation with the upstream pytest setup.

## 7. Findings

1. An immediate `RenameField` migration exits successfully and the existing test suite passes on the new code, yet the old application breaks for every request that loads a `User` row. Neither signal on its own would have caught this.
2. A database trigger that syncs in both directions kept both versions consistent across 10 interleaved cases. A sync that reacts only to the new column left old writes invisible to the new application, which confirms that dual-writing in the new application does not cover old writers.
3. The retirement gate turned a fatal ordering mistake by the live model into an actionable failure, and the model fixed it in one revision.
4. Gathering evidence through real servers with GraphQL calls costs about 20 to 30 seconds per plan attempt once the seed exists, which is cheap enough to run in a loop.

## 8. Limits and remaining gaps

1. One field, one rename pattern, one live model run. Nothing here supports a claim that the multi-agent structure outperforms a single model or a human reviewer.
2. The scripted v1 flaw was planted by hand. The live model's flaw was ordering, not consistency, so the loop has not shown a model diagnosing a sync bug.
3. Interleaving is sequential request by request. Truly concurrent writes, lock behaviour, and backfill timing under load were not measured. `ADD COLUMN` was nullable, so it was a metadata-only change on 245 rows.
4. The trigger case where both columns change is exercised only through raw SQL, because neither application version writes both columns.
5. Celery tasks, webhooks, read replicas, and the old app's other `User` paths (such as login and bulk update through the old version) were not exercised individually.
6. The live model's two-trigger design relies on `UPDATE OF <column>` and the alphabetical firing order of triggers. It passed the runner's checks, but I did not review it for edge cases beyond them.
7. The investigator ground truth was written by the same person who wrote the patch, and the scripted investigator copies it, so its score of 1.0 carries no information.
8. Model output is parsed only as JSON and its SQL runs only on local `migproto_run_*` databases behind a denylist. This is a prototype guard, not a sandbox, and it would need a stricter allowlist before running against anything non-disposable.

Prior art: upstream already runs `.github/workflows/test-migrations-compatibility.yml`, which applies a PR's migrations and then runs the base branch's tests. That would plausibly catch the unsafe rename in section 4.2, but it does not exercise interleaved old and new writers or retirement ordering.
