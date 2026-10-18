# Agent and Serving Results: Nemotron 3.5 Lightning on vLLM (Brev)

DRAFT, 2026-10-17 22:10 EDT. Configurations A-D and scenario sweeps 1-2 are complete; configuration E is running.

Every number below comes from saved run directories (`runs/<run_id>/summary.json`,
`events.jsonl`) and batch records (`runs/brev-batch-*/runs.jsonl`, `CONFIG-NOTE`,
`INFRA-NOTE`).

- **Rows:** one per review, failures included.
- **Development runs:** local stand-in model runs (`docs/local-standin-runs.md`) and the
  `gpt-oss:120b-cloud` smoke test are development runs and stay out of these tables.
- **No claims of:** topology superiority beyond the counts shown, concurrency safety, zero
  downtime, or general migration support.

## Setup (all configurations)

| Field | Value |
|---|---|
| Model | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` @ `bee7596271d1495f6992ae224aefde4410e816b8`, served as `nemotron-3.5-lightning` |
| Serving | vLLM 0.27.1, 1x NVIDIA H100 PCIe (Brev `scaleway_H100`, event credits); full record in `docs/serving.md` |
| Sampling | temperature 1.0, top_p 0.95, reasoning on (`nemotron_v3` parser), `force_nonempty_content` |
| Runner | local Linux laptop; Saleor pinned `0c0e2697`; synthetic seed snapshot `seed:61e4e7a7f3caa544` (245 users); sequential interleaving of old and new app requests within a review |
| Parallelism | 1-9 reviews at once on one vLLM server (separate app ports, locks, Redis DBs and run databases per worker); each review is still sequential internally. Model latency per call depends on that load: 15-30 s per call with 2-5 concurrent reviews, 85-120 s with 8-9 (per-run figures from `summary.json` `agent_totals`) |

## How the configuration was reached (smoke tests, not results)

| Setting | Outcome | Change made |
|---|---|---|
| Reasoning uncapped, 40k-token budget | both runs INCOMPLETE after 2-4 calls (18-24k completion tokens per call) | added `MIGPROTO_LLM_THINKING_BUDGET` (vLLM `thinking_token_budget`) |
| Reasoning off | both runs ERROR: invalid plans | the response schema allowed `"sql": []` on deploy/retire stages, which the runner's validator rejects; schema fixed to separate stage shapes |
| Cap 4,096, 120k tokens, 1 revision | `roles` FAIL, `single` FAIL (reached the runner) | prompt now states two runner facts (SQL is sent verbatim; repeated statements must stop matching rows). Revision limit made configurable |

These runs are listed in the AGENTS.md quick start. They are not part of the counts below.

## Configuration A: rename scenario (`rename-note`)

**Settings:**
- Reasoning cap 16,384 tokens per call.
- Up to 3 revisions, 16 model calls, 300,000 total tokens and 1,800 s per review.
- Identical budget for `roles` and `single`.
- Batches: `runs/brev-batch-20261017-184356`, `-185514` and `-191348`.

| Topology | Reviews | PASS | FAIL | INCOMPLETE | Excluded (infrastructure) |
|---|---|---|---|---|---|
| roles | 5 | **2** | 3 | 0 | 3 |
| single | 7 | 0 | 0 | **7** (token budget) | 1 |

Excluded runs: a network outage on the runner laptop killed the SSH tunnel at about 19:43
(`runs/brev-batch-20261017-191348/INFRA-NOTE`). Those 4 runs ended ERROR with "connection
refused" and say nothing about the model.

**Per review.** The arrows show failed-check counts per attempt. Each review had up to 4
attempts.

| Run | Topology | Attempts (failed checks) | Calls | Tokens | Review time | Verdict |
|---|---|---|---|---|---|---|
| `20261017-184415-agent-loop-llm` | roles | 16 → 16 → 13 → **0** | 13 | 162,509 | 611 s | PASS |
| `20261017-190133-agent-loop-llm` | roles | 20 → 13 → **0** | 10 | 136,342 | 520 s | PASS |
| `20261017-191411-agent-loop-llm` | roles | 1 → 1 → 13 → 5 | 13 | 141,485 | 934 s | FAIL |
| `20261017-191417-agent-loop-llm` | roles | 18 → 1 → 13 → 6 | 14 | 167,695 | 941 s | FAIL |
| `20261017-192307-agent-loop-llm` | roles | 15 → 9 → 3 → 6 | 14 | 192,713 | 1,046 s | FAIL |
| `20261017-185533-agent-loop-llm` | single | 1 → 18 → 1 | 13 | 323,662 | 359 s | INCOMPLETE |
| `20261017-191014-agent-loop-llm` | single | 1 → 18 → 24 | 12 | 302,252 | 432 s | INCOMPLETE |
| `20261017-191408-agent-loop-llm` | single | 1 → 17 → 15 | 12 | 315,549 | 532 s | INCOMPLETE |
| `20261017-191414-agent-loop-llm` | single | 1 → 18 → 14 | 13 | 342,720 | 532 s | INCOMPLETE |
| `20261017-192301-agent-loop-llm` | single | 1 → 16 → 1 | 12 | 320,246 | 490 s | INCOMPLETE |
| `20261017-192946-agent-loop-llm` | single | 1 → 17 → 1 | 13 | 318,500 | 479 s | INCOMPLETE |
| `20261017-193111-agent-loop-llm` | single | 1 → 1 → 24 | 13 | 332,638 | 633 s | INCOMPLETE |

**What A shows:**
- **Both passing `roles` reviews fixed their own bugs from runner evidence.** The first, for
  example: no sync → an `AFTER` trigger, which cannot change `NEW` → a `BEFORE` trigger that
  copied only into NULLs → change detection against `OLD`.
- **All three `roles` FAILs ended on the trigger's INSERT path.** A new-app insert had its
  `customer_note` overwritten (`case.new_create_old_read` and tester-proposed checks such as
  `new_insert_null_wipe`).
- **A controller defect hid evidence in these revisions.** Revision feedback was truncated to
  10 failed checks and 8,000 characters. The revising planner saw 4 of 13 failures (and once 4
  of 18), and the tester summary was cut off entirely. Fixed in `3bdd794`, which is
  configuration B.
- **`single` never finished:** about 250k of its roughly 310k tokens were prompt tokens,
  because it resends its whole history on every call. This is a cost of how `single` carries
  context, not evidence about reasoning quality.

## Configuration B: rename, complete runner feedback

Batch `runs/brev-batch-20261017-204244` (4 workers, up to 9 concurrent reviews on the GPU).
Settings are A's plus compact, complete runner feedback (tester summary first, every failed
check). Traced in Langfuse (environment `brev`).

| Topology | Valid reviews | PASS | FAIL | INCOMPLETE | Excluded |
|---|---|---|---|---|---|
| roles | 4 | **1** | 2 | 1 (wall time) | 1 (run-directory collision) |
| single | 5 | 0 | 1 | **4** (token budget) | 0 |

| Run | Topology | Attempts (failed checks) | Calls | Tokens | s/call | Verdict |
|---|---|---|---|---|---|---|
| `20261017-204305-agent-loop-llm` | roles | 15 → 20 → 24 → **0** | 13 | 200,226 | 106.4 | PASS |
| `20261017-204311-agent-loop-llm` | roles | 2 → 15 → 1 → 15 | 13 | 160,131 | 87.5 | FAIL |
| `20261017-211628-agent-loop-llm` | roles | 2 → 17 → 8 → 15 | 13 | 174,796 | 86.7 | FAIL |
| `20261017-205627-agent-loop-llm` | roles | 1 → 1 → 1 | 13 | 173,093 | 121.1 | INCOMPLETE (1,800 s) |
| `20261017-210417-agent-loop-llm` | roles | excluded: shared its run directory with another review | | | | excluded |
| `20261017-204308-agent-loop-llm` | single | 4 attempts | 13 | 305,854 | 57.0 | INCOMPLETE (tokens) |
| `20261017-204315-agent-loop-llm` | single | 3 attempts | 13 | 309,452 | 41.0 | INCOMPLETE (tokens) |
| `20261017-205951-agent-loop-llm` | single | 3 attempts | 13 | 335,135 | 67.5 | INCOMPLETE (tokens) |
| `20261017-210850-agent-loop-llm` | single | 3 attempts | 14 | 307,939 | 56.4 | INCOMPLETE (tokens) |
| `20261017-212336-agent-loop-llm` | single | 1 → 1 → 1 → 1 | 13 | 288,493 | 38.5 | FAIL |

## Configuration C: `single` with compacted history

Batch `../hackathon-variant/runs/brev-batch-20261017-211542` (1 worker). Settings are B's plus:
- the `single` history compaction `latest-plan-evidence/v1` (`docs/decisions/single-history.md`);
- the deploy-variant guard (see Disclosures).

The `roles` rename prompts are byte-identical to B's.

| Run | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|
| `20261017-211603-agent-loop-llm` | 1 → 1 → 1 → 1 | 169,492 | FAIL |
| `20261017-212539-agent-loop-llm` | 1 → 1 → 1 → 1 | 162,255 | FAIL |
| `20261017-213326-agent-loop-llm` | 1 → 16 → 1 → 23 | 224,190 | FAIL |

**What C shows:**
- **Budget:** all 3 reviews finished within budget. Before compaction, 9 of 10 `single` reviews
  in A and B ran out of tokens.
- **Builds:** 9 of the 12 plans set `"migration": "apply"`. That deploys the build whose Django
  migration runs the rename SQL itself, so it collided with the plan's own column
  (`django_migrate`).
- **Why:** the rename prompt never said what the `migration` field selects, while every other
  scenario's prompt did. The `roles` planner never set the field, so it got the `state_only`
  default.

## Configuration D: rename prompt lists the deployable builds

Batch `../hackathon-configd/runs/brev-batch-20261017-213725`. Settings are C's plus the rename
contract listing `state_only` (the default) and `apply`, in the same words every other
scenario's prompt uses (`ebe6f40`). It says what each build does, not which one to pick.
Rename prompts differ from A-C from here on.

| Run | Topology | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|---|
| `20261017-214321-agent-loop-llm` | roles | 17 → 1 → **0** | 133,710 | PASS |
| `20261017-213745-agent-loop-llm` | roles | 1 → 17 → 1 → 1 | 139,047 | FAIL |
| `20261017-213748-agent-loop-llm` | single | 20 → 14 → 15 → 16 | 235,439 | FAIL |
| `20261017-215324-agent-loop-llm` | single | 4 attempts | 254,520 | FAIL |

The batch was stopped after 4 reviews to apply configuration E's fixes; one more `roles` review
was still running at the time of writing.

**What D shows:**
- **Build choice:** every `single` plan chose `state_only`.
- **What still fails:** two-way sync between `note` and `customer_note`, in particular clears and
  the INSERT path. This is the same remaining problem the `roles` reviews have.

## Why rename reviews fail (A-D)

From the per-attempt failed checks and the plans (`agents/planner-*.json`) of every rename
review:

1. **Revisions regress.** Every failing `roles` review reached 1-3 failed checks at some attempt,
   then the next revision got worse (for example 15 → 9 → **3** → 6 and 2 → 15 → **1** → 15).
   The review verdict is the final attempt, and the revising planner saw only its latest plan.
2. **SQL that never executed.** Many attempts failed only `stage.*.executes`, so nothing else in
   them was tested:
   - backslash-escaped `\$\$`, although the prompt says not to;
   - a `$$` function body split across several SQL strings;
   - other syntax errors.
3. **Trigger semantics.** The remaining failures are the two-way sync itself. A passing trigger
   compares `NEW` with `OLD` for each column. Near-misses treat an empty note as unchanged
   (`COALESCE(OLD.note, '')`) or get the INSERT path wrong.

Configuration E addresses 1 and 2. It leaves 3 to the model.

## Configuration E: SQL quoting pre-check and best-attempt context (RUNNING)

Batches `runs/brev-batch-20261017-220007` (rename: 3 `roles` + 3 `single`) and
`runs/brev-batch-20261017-220011` (scenarios without a match: irreversible-data-migration,
raw-sql-rename, shorten-note). Code `c477869` (on `main` as PR #6). Settings are D's plus:
- **SQL quoting pre-check:** an agent-side validator rejects backslash-escaped dollar quotes and
  unbalanced `$$`, so the model corrects the plan in its retry. Replayed on the 160 plans
  recorded before E, it caught all 7 execution failures caused by quoting and rejected no plan
  that had executed. Other SQL errors still reach the runner.
- **Best-attempt context:** when the latest attempt passed fewer runner checks than an earlier
  one, the revision request also includes the best earlier plan, its counts and its failed
  check names. Same for `roles` and `single`.
- **Exclusive run directories.**

No runner check changed. Results pending.

## Scenario sweeps (`roles`, one review per scenario)

Scored with `scripts/brev/score_scenarios.py` against Mahek's manual expected verdicts
(`proposals/manual/scenarios/`), which agents never see. The target is the best listed outcome:
a PASS with a listed recommendation when a safe plan exists, otherwise a rejection.

**Sweep 1** (`runs/brev-batch-20261017-204405`, configuration B code):

| Scenario | Runner verdict | Runner / agent rec. | Attempts (failed checks) | Tokens | Matches |
|---|---|---|---|---|---|
| add-optional-column | PASS | accept_as_is / accept_as_is | 0 | 18,865 | yes |
| add-table | PASS | accept_as_is / accept_as_is | 0 | 20,241 | yes |
| lengthen-varchar | PASS | accept_as_is / accept_as_is | 0 | 38,014 | yes |
| add-required-column | PASS | staged_rollout / staged_rollout | 0 | 39,570 | yes |
| foreign-key-orphans | PASS | staged_rollout / staged_rollout | 2 → 0 | 66,839 | yes |
| unique-last-name | FAIL | reject / reject | 1 → 1 → 1 | 118,328 | yes (no safe plan exists) |
| drop-column | FAIL | reject / staged_rollout | 13 → 12 → 9 → 10 | 154,765 | no: variant bug |
| backfill-then-required | FAIL | reject / staged_rollout | 2 → 2 → 2 → 2 | 121,489 | no: variant bug |
| shorten-note | INCOMPLETE (1,800 s) | reject / staged_rollout | 4 → 4 | 101,541 | no: variant bug |
| irreversible-data-migration | excluded | | | | run-directory collision |

**Sweep 2** (`../hackathon-variant/runs/brev-batch-20261017-212229`, with the variant guard): it
reran the three variant-bug scenarios and added three not yet run.

| Scenario | Runner verdict | Runner / agent rec. | Attempts (failed checks) | Tokens | Matches |
|---|---|---|---|---|---|
| drop-column | PASS | staged_rollout / staged_rollout | 0 | 53,042 | yes |
| backfill-then-required | PASS | staged_rollout / staged_rollout | 0 | 29,669 | yes |
| rename-db-column | PASS | accept_as_is / accept_as_is | 0 | 37,499 | yes |
| add-index-concurrently | PASS | accept_as_is / accept_as_is | 0 | 42,587 | yes |
| shorten-note | FAIL | reject / accept_as_is | 3, then an invalid revision | 87,586 | no |
| raw-sql-rename | FAIL | reject / staged_rollout | 16 → 1 → 7 → 2 | 200,580 | no |

**Coverage:** 10 of the 13 catalogue scenarios have matched the expected outcome in at least one
valid review. Shorten-note and raw-sql-rename have not. Irreversible-data-migration has no valid
result yet (it is rerun in configuration E). One review per scenario per sweep: these are
single observations, not rates.

**Shorten-note (sweep 2):**
- The agent recommended `accept_as_is` and deployed the build that shortens the column. Existing
  notes are longer than 255 characters, and the runner reported them lost
  (`deploy_new.preservation.note`).
- Its revision kept `accept_as_is` while adding SQL stages. The validator rejects that
  combination, and the review ended FAIL as "revision invalid".

## Disclosures

- **Prompt content added during development.** None of it states a solution:
  - the runner-contract lines (SQL is sent verbatim; repeated statements must stop matching
    rows);
  - for scenarios other than the rename, a general Django fact: a column the running model
    does not know gets its database DEFAULT or NULL on INSERT. This bears on A4.
- **A4 plan:** the plan keeps the column's database default and uses `SELECT 1;` as its
  contract stage. The runner passed it, but that stage satisfies the "at least one contract
  stage" rule without doing anything. Mahek's reference plan drops the default instead.
- **Deploy-variant bug (fixed in `30a6304`):**
  - The plan schema let a plan set `"migration": "state_only"` together with
    `"variant": "apply"`. The runner deploys the explicit variant, so the apply build ran.
  - Sweep 1's drop-column, backfill-then-required and shorten-note failed on this. The fault was
    in our plan interface, not in the model's migration reasoning.
  - The fix: schema and validator now allow `variant` only for named alternative builds. All
    three scenarios were rerun in sweep 2.
- **Run-directory collision (fixed in `4ade067`):**
  - Two supervisors sharing one `runs/` directory started reviews in the same second, and both
    logged into `runs/20261017-210417-agent-loop-llm`: B's rename review 7 and sweep 1's
    irreversible-data-migration.
  - Both are excluded and recorded in each batch's `INFRA-NOTE`. Run directories are now created
    exclusively.
- **Concurrency:** reviews that stopped on the 1,800 s wall-time budget (B 205627, sweep 1
  shorten-note) ran while 8-9 reviews shared the GPU, at 100-121 s per model call. They are
  reported as INCOMPLETE, not as model failures, and not as passes.
- **Irreversible-data-migration** is not a model result. Before the collision overwrote it,
  `score_scenarios.py` read it as a runner PASS whose plan picked the reversible build but added
  an unneeded backfill stage (`staged_rollout` rather than the expected `accept_as_is`).
- **Configurations differ:** rename prompts differ between A-C and D-E (build lines) and in E
  (best-attempt context, only when a revision regresses). Results from different
  configurations are never pooled.
- **Sample size:** 4-7 reviews per topology and configuration on rename, one per scenario per
  sweep. These are counts under the stated conditions, not rates with confidence intervals.
