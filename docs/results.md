# Agent and Serving Results: Nemotron 3.5 Lightning on vLLM (Brev)

DRAFT, 2026-10-18 06:30 EDT. Configurations A-H, M and scenario sweeps 1-2 are complete;
configuration I is running.

**Pre-registered comparison (configuration H, 72 reviews, 9 scenarios):** `parallel` matched
the expected outcome in 21 of 36 reviews, `single` in 19 of 36. Two-sided Fisher exact
p = 0.81, so there is no measured advantage for the multi-agent topology (details under
Configuration H).

**Where things stand (rename scenario, reviews that passed):**

| Configurations | `roles` | `single` | Notes |
|---|---|---|---|
| A-E | 3 of 14 valid | 0 of 20 | configurations differed between topologies |
| F | 0 of 3 | 1 of 3 | runner facts first |
| G | 0 of 3 | 1 of 3 | syntax pre-check, larger budget; `single` out of tokens once |
| M (G + RAG) | 1 of 3 | 0 of 3 | every `single` review out of tokens |

Small samples under changing configurations: counts, not rates, and not a topology
comparison. Configuration H is the pre-registered comparison. Scenario sweeps: 10 of the 13
catalogue scenarios matched the expected outcome in at least one `roles` review.

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

Batch `runs/brev-batch-20261017-211542` (1 worker). Settings are B's plus:
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

Batch `runs/brev-batch-20261017-213725`. Settings are C's plus the rename
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

1. **Revisions regress.** Failing `roles` reviews often had an attempt with 1-3 failed checks,
   then a worse revision (for example 15 → 9 → **3** → 6 in `192307` and 2 → 15 → **1** → 15 in
   `204311`). The review verdict is the final attempt, and the revising planner saw only its
   latest plan. **Caveat (found in an independent audit of these runs):** a low count is not
   always a near-miss. Many 1-2 failure attempts halted early (a backfill that never reached 0
   rows, a retirement-gate refusal or a failed `django_migrate`), so the cross-version checks
   never ran; failure counts are only comparable between attempts that ran the same stages.
2. **SQL that never executed.** Many attempts failed only `stage.*.executes`, so nothing else in
   them was tested:
   - backslash-escaped `\$\$`, although the prompt says not to;
   - a `$$` function body split across several SQL strings;
   - other syntax errors.
3. **Trigger semantics.** The remaining failures are the two-way sync itself. A passing trigger
   compares `NEW` with `OLD` for each column. Near-misses treat an empty note as unchanged
   (`COALESCE(OLD.note, '')`) or get the INSERT path wrong.

Configuration E addresses 1 and 2. It leaves 3 to the model.

## Configuration E: SQL quoting pre-check and best-attempt context

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

No runner check changed.

**Rename: 0 of 6 passed.**

| Run | Topology | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|---|
| `20261017-220028-agent-loop-llm` | roles | 1 → 19 → 18 → 1 | 175,763 | FAIL |
| `20261017-220921-agent-loop-llm` | roles | 1 → 17 → 17 → 1 | 152,062 | FAIL |
| `20261017-222313-agent-loop-llm` | roles | 1 → 17 → 15 → 16 | 154,580 | FAIL |
| `20261017-220031-agent-loop-llm` | single | 18 → 14 → 17 → 15 | 260,669 | FAIL |
| `20261017-221841-agent-loop-llm` | single | 1 → 18 → 15 → 12 | 218,793 | FAIL |
| `20261017-222524-agent-loop-llm` | single | 1 → 1 → 1 → 1 | 151,631 | FAIL |

Every review used 13 of 16 calls and ended at `max_revisions` within the token and wall budgets.

**What E shows:**
- **Attempt 1 rarely tested anything:** 5 of the 6 first attempts failed only
  `stage.*.executes`, each a `repeat_until_zero_rows` backfill that never reached 0 rows.
  `222524` then hit the retirement gate in attempts 2-4.
- **The best-attempt context misled at least once:** in `220028`, attempt 1 had the sync trigger
  in the right place (before `deploy_new`) and halted only on its backfill. The planner, also
  told by the tester's report that the trigger was at fault, moved the trigger after
  `deploy_new`; the evidence then showed `triggers: []` at `deploy_new` for every failed
  cross-version check. The best-attempt context ranked by passed checks, so it pointed at
  attempt 2 (20 passed, 18 failed) and never at attempt 1, which ran fewer checks.
- **Last attempts were close twice:** `220028` ended on 1 failure (the trigger function still
  referenced `NEW.note` after the contract stage dropped it), and `220921` on 1 (its backfill
  again).

**Scenarios: 0 of 3 matched.**

| Scenario | Runner verdict | Runner / agent rec. | Attempts (failed checks) | Tokens | Matches |
|---|---|---|---|---|---|
| irreversible-data-migration | PASS | staged_rollout / staged_rollout | 1 → 1 → 1 → 0 | 133,972 | no: expected accept_as_is |
| raw-sql-rename | FAIL | reject / staged_rollout | 17 → 1 → 1 → 16 | 153,035 | no |
| shorten-note | FAIL | reject / reject | 2 (revision skipped) | 36,267 | no: expected a staged PASS |

- **Irreversible-data-migration:** a safe plan, but with an unneeded backfill, so the
  recommendation is `staged_rollout` rather than the expected `accept_as_is`.
- **Raw-sql-rename:** attempts 2-3 failed on SQL errors (`EXECUTE FUNCTION f;` without `()`,
  and a statement-level trigger with a `WHEN` condition).
- **Shorten-note:** the planner recommended `reject` at once; the runner also rejected the
  plan, so the revision was skipped.

## Configuration F: runner facts first, earlier attempts as facts, backfill pre-check

Batch `runs/brev-batch-20261017-223954` (rename: 3 `roles` + 3 `single`). Code `3e7e73f`
(branch `fix/feedback-ranking`), run from the main checkout. Settings are E's with the planner
feedback changed (decision D3 in `docs/decisions/single-history.md`):
- **Runner evidence first,** then the tester's report labelled as an unverified hypothesis that
  loses to the runner evidence.
- **Earlier attempts as facts:** every earlier attempt's halt, halted step, stages run, failed
  check names and plan, with a note that counts are not comparable across attempts that halted
  at different stages. The passed-checks "best attempt" is gone.
- **Backfill pre-check:** an agent-side validator rejects a `repeat_until_zero_rows` column copy
  whose `WHERE` the update does not make false. On the 203 recorded Lightning planner replies it
  flagged all 18 whose backfill hit the runner's 10,000-batch stop, none of the 25 whose backfill
  converged, and 8 whose backfill never ran.

No runner check changed.

**Rename: 1 of 6 passed.** Configuration G's `single` review `230418`, running at the same
time, also passed.

| Run | Topology | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|---|
| `20261017-224014-agent-loop-llm` | roles | none executed (planner-1 invalid) | 70,146 | ERROR |
| `20261017-224458-agent-loop-llm` | roles | 2 → 13 → 11 → 9 | 187,824 | FAIL |
| `20261017-225329-agent-loop-llm` | roles | 15 → 1 → 1 → 1 | 175,145 | FAIL |
| `20261017-224017-agent-loop-llm` | single | 16 → 15 → 10 → 8 | 262,686 | FAIL |
| `20261017-224645-agent-loop-llm` | single | 1 → 17 → 1 → 16 | 222,629 | FAIL |
| `20261017-230949-agent-loop-llm` | single | 14 → 9 → 1 → **0** | 260,320 | PASS |

**What F shows:**
- **The `single` pass improved at every attempt:** `230949` went 14 → 9 → 1 → 0 within the
  default 3 revisions. Each tester report matched the runner evidence: a one-way `COALESCE`
  trigger, then INSERT and NULL-clear handling, then a trigger function still referencing
  `note` after the contract stage dropped it, then a pass. It used 260,320 of 300,000 tokens.
  One review: not a rate.
- **First attempts got past the backfill more often:** in 3 of the 4 reviews that executed a
  plan, attempt 1 ran past the backfill (E: 1 of 6). Each still failed cross-version reads or
  the retirement gate.
- **Steadier revisions in two reviews:** `224017` (16 → 15 → 10 → 8) and `224458`
  (2 → 13 → 11 → 9) improved after their worst attempt instead of swinging back. Both ran out of
  revisions.
- **Low counts are still not near-misses:** `225329`'s three 1-failure attempts each halted on
  the same SQL error, `INSERT trigger's WHEN condition cannot reference OLD values` (one
  `INSERT OR UPDATE` trigger with `WHEN (NEW.note IS DISTINCT FROM COALESCE(OLD.note, ...))`).
  The SQL parses, so the syntax pre-check would not catch it; PostgreSQL rejects it when the
  trigger is created. `224645`'s 1-failure attempts were a retirement-gate refusal and a stage
  that failed to execute.

**Side effect seen in F:** `20261017-224014` (roles) ended ERROR: planner-1's three replies each failed validation (unbalanced `$$`, an empty `sql` list, then the backfill pre-check), so no plan reached the runner. Without the backfill pre-check the third plan would have run. Fixed on `fix/next` (pre-checks are advisory on the final retry).

**Next (not in F, on branch `fix/next`):** a PostgreSQL syntax pre-check (`pglast`). On the same
203 replies it flagged all 18 stages that failed with a syntax error and none of the 181 others.

## Configuration G: syntax pre-check, advisory pre-checks, larger budget

Batch `runs/brev-batch-20261017-230351` (rename: 3 `roles` + 3 `single`). Code `fix/next`
(`feec876`), run from worktree `../hackathon-fixes` (workers 2-3); runs copied unchanged into
`runs/`. Settings are F's plus the PostgreSQL syntax pre-check (`pglast`), pre-checks advisory
on the final retry, and a larger budget: `max_revisions` 5, 22 calls, 450,000 tokens, 2,700 s,
`THINKING_BUDGET=16384`. Up to 6 reviews (F's last two, G, M) shared the vLLM server.

**Rename: 1 of 6 passed.**

| Run | Topology | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|---|
| `20261017-230415-agent-loop-llm` | roles | 1 → 15 → 15 → 17 → 15 → 17 | 335,465 | FAIL |
| `20261017-232229-agent-loop-llm` | roles | 15 → 20 → 1 → 1 → 1 | 326,478 | INCOMPLETE (22 calls) |
| `20261017-234853-agent-loop-llm` | roles | 18 → 16 → 18 → 19 → 15 → 18 | 342,542 | FAIL |
| `20261017-230418-agent-loop-llm` | single | 16 → 1 → 1 → 1 → 1 → **0** | 369,295 | PASS |
| `20261017-233733-agent-loop-llm` | single | 1 → 15 → 20 → 1 → 17 → 15 | 374,632 | FAIL |
| `20261017-234913-agent-loop-llm` | single | 17 → 21 → 22 → 17 → 17 | 461,976 | INCOMPLETE (tokens) |

- **More revisions did not settle the reviews:** four of the six swung between 1 and 15-22
  failures across attempts.
- **`single` hit the token budget once** with the larger budget (450k).

## Configuration M: G plus prior evidence (RAG) for planner-1

Batch `runs/brev-batch-20261017-230355`. Code: G plus `feat/prior-evidence` (`e649e85`, decision
D4 on that branch, not on `main`): planner-1 also gets failed, refused and errored runner
observations from earlier Lightning runs of the same change (cutoff `20261017-230351`; the first
review loaded 27 runs, 757 observations, 5,525 characters). Run from worktree `../hackathon-rag`
(workers 4-5); runs copied unchanged into `runs/`. Same budget as G.

**Rename: 1 of 6 passed.**

| Run | Topology | Attempts (failed checks) | Tokens | Verdict |
|---|---|---|---|---|
| `20261017-230419-agent-loop-llm` | roles | 1 → **0** | 105,450 | PASS |
| `20261017-231816-agent-loop-llm` | roles | 16 → 2 → 23 → 17 → 15 → 18 | 286,796 | FAIL |
| `20261017-232905-agent-loop-llm` | roles | 16 → 16 → 8 → 9 → 7 → 8 | 261,510 | FAIL |
| `20261017-230422-agent-loop-llm` | single | 15 → 21 → 16 → 17 → 17 | 473,861 | INCOMPLETE (tokens) |
| `20261017-231832-agent-loop-llm` | single | 17 → 24 → 20 → 19 → 19 | 455,968 | INCOMPLETE (tokens) |
| `20261017-234232-agent-loop-llm` | single | 19 → 16 → 19 → 18 → 16 | 466,967 | INCOMPLETE (tokens) |

- **`roles` `230419` passed on attempt 2** with the fewest tokens of any rename pass (105,450).
- **Every `single` review ran out of tokens.** Prompts averaged 20-22k tokens per call (G
  `single`: 13-21k), consistent with the prior-evidence section staying in the single agent's
  history; not verified further.
- **G and M together:** `single` exhausted the token budget in 4 of its 6 reviews; `roles` never
  did (one stopped on the call budget). One review per cell differs between G and M, so these
  are counts, not a comparison of RAG.

## Configuration H: `parallel` versus `single` (pre-registered)

Written and committed before the batch started; the results will be added below this
paragraph without changing it.

- **Arms:** `parallel` (D5: three planners per round, the runner executes every valid plan)
  and `single` (D1 compacted history). Same model, serving, sampling (temperature 1.0, top_p
  0.95, `THINKING_BUDGET=16384`), prompts, pre-checks (quoting, syntax, trigger `WHEN`,
  backfill) and plan rules (no-filler, `31c2cf7`). Prior evidence (RAG) is off for both.
- **Budget, identical for both:** `max_revisions` 5, 40 model calls, 450,000 tokens, 3,000 s.
- **Scenarios (9), chosen before running:** every catalogue scenario that in earlier sweeps
  needed a revision, a rejection or a staged plan: rename-note, raw-sql-rename, shorten-note,
  irreversible-data-migration, foreign-key-orphans, unique-last-name, drop-column,
  backfill-then-required, add-required-column. Excluded because they passed on attempt 1
  with `accept_as_is` and cannot separate the topologies: add-optional-column, add-table,
  lengthen-varchar, rename-db-column, add-index-concurrently.
- **Runs:** 4 per scenario per topology (72 reviews), interleaved scenario by scenario so
  both arms share the same load.
- **Primary metric:** reviews whose outcome matches Mahek's expected outcome
  (`scripts/brev/score_scenarios.py`, `proposals/manual/scenarios/`, never shown to agents;
  rename-note: runner PASS with `staged_rollout`), per topology, out of 36.
- **Decision rule:** `parallel` is reported as better only if it matches more reviews and a
  two-sided Fisher exact test on the two 2x2 counts gives p < 0.05. Otherwise the counts are
  reported as not distinguishable at this sample size.
- **Secondary (reported, not decisive):** runner PASS count, budget-exhausted (INCOMPLETE)
  count, median tokens per review, tokens per matched review, wall time.
- **Exclusions:** none, except runs recorded in the batch's INFRA-NOTE as infrastructure
  failures (listed with their cause). Every other review is reported, including failed
  attempts.

**Status (2026-10-18 00:30):** running, batch `runs/brev-batch-20261018-000918` from worktree
`../hackathon-final` (workers 4-8), code `19dd71f`. No results yet.

**Status (01:35):** split across two runner machines, recorded in the laptop batch's
`INFRA-NOTE` at the time. Same code (`19dd71f`, confirmed on both checkouts), flags, budgets
and the one vLLM server on `horizontal-jade-beetle`.
- The laptop batch `runs/brev-batch-20261018-000918` ran runs 1-2 of every scenario and
  topology.
- Mahek's machine (WSL2, 3 workers) ran runs 3-4 as batch `runs/brev-batch-20261018-013233`.
  It started at 01:33. Both halves' batch records and run directories were copied unchanged
  (`cp -a`) into this checkout's `runs/`.
- From 01:33, up to 8 reviews shared the server instead of 5. Calls were slower, which
  matters for the 3,000 s cap.

### Configuration H: results (both halves done 04:00)

**Primary (pre-registered):** `parallel` matched the expected outcome in **21 of 36**
reviews, `single` in **19 of 36**. Two-sided Fisher exact **p = 0.81**, so the topologies are
**not distinguishable at this sample size**. No superiority claim either way. The halves
agree: laptop 11/18 vs 10/18, Mahek's 10/18 vs 9/18.

| Scenario (expected outcome) | `parallel` | `single` |
|---|---|---|
| rename-note (PASS, `staged_rollout`) | 3/4 | 0/4 |
| raw-sql-rename (PASS, `staged_rollout`) | 0/4 | 0/4 |
| shorten-note (PASS, `staged_rollout`) | 0/4 | 0/4 |
| irreversible-data-migration (PASS, `accept_as_is`) | 0/4 | 0/4 |
| foreign-key-orphans (PASS, `staged_rollout`) | 4/4 | 4/4 |
| unique-last-name (FAIL, `reject`) | 2/4 | 4/4 |
| drop-column (PASS, `staged_rollout`) | 4/4 | 4/4 |
| backfill-then-required (PASS, `staged_rollout`) | 4/4 | 3/4 |
| add-required-column (PASS, `staged_rollout`) | 4/4 | 4/4 |

The per-scenario differences (rename-note for `parallel`, unique-last-name for `single`) are
4 reviews per cell and are not tested separately.

**Secondary (reported, not decisive):**

| | `parallel` | `single` |
|---|---|---|
| Runner verdicts | 22 PASS, 2 FAIL, 12 INCOMPLETE | 18 PASS, 13 FAIL, 4 INCOMPLETE, 1 ERROR |
| Stopped by token cap / wall cap | 7 / 5 | 4 / 0 |
| Median tokens per review | 212,269 | 154,075 |
| Tokens per matched review (total / matched) | 415,000 (8.72 M / 21) | 361,000 (6.85 M / 19) |
| Median wall time per review | 1,715 s | 593 s |
| Total review wall time | 15.0 h | 8.5 h |

- **irreversible-data-migration:** 6 runner PASSes (3 per topology) are scored as not matched.
  Each recommended `staged_rollout` where `accept_as_is` was expected.
- **Budget overruns:** 7 `parallel` and 4 `single` reviews ended above 450,000 tokens. The
  largest overrun was 58,998 tokens (`parallel`) and 22,828 (`single`). The budget is
  checked between calls, so the call in progress finishes. Reviews stopped by the wall cap
  ended at 3,000 s.
- **PASS audit:** an independent script re-read all 40 runner PASSes from `events.jsonl` and
  the saved plans (21 laptop, 19 Mahek's). None was flagged. It checked that the cross-version
  cases ran, that old-app retirement was confirmed before any contract stage, and that the
  recommendation was recorded.
- **Contract stage:** 18 of the 40 PASSes (9 `parallel`, 9 `single`) have no SQL contract
  stage: the plan leaves the old shape in place. The runner accepts this, and the scorer only
  reads verdict and recommendation, so a matched PASS does not mean the migration was
  finished.

**Exclusion not applied:** `single` shorten-note `20261018-020659-agent-loop-llm` (Mahek's
half) ended in ERROR because the runner found port 8011 still in use ("stop stray
servers first"). That is an infrastructure failure, not a model result. It was not recorded
in an `INFRA-NOTE`, so under the pre-registered rule it stays in and counts as not matched.
Excluding it gives 21/36 vs 19/35, p = 0.81; the conclusion is unchanged.

### Configuration H2: longer wall-time cap (exploratory, pre-registered 01:25)

Decided after seeing H's first 18 reviews, so it is **exploratory** and never changes H's
verdict. Of those 18, two `parallel` reviews stopped on a budget while close (shorten-note on
the 3,000 s cap at 1-2 failed checks; raw-sql-rename on the token cap at 1). H2 asks only
whether more wall time changes the outcome on the rename-type scenarios.

- **Same as H** in code (`19dd71f`), serving, sampling, prompts, pre-checks and every budget
  except one: `max_wall_seconds` 4,500 instead of 3,000. Tokens stay 450,000, calls 40,
  revisions 5.
- **Scenarios (3):** rename-note, raw-sql-rename, shorten-note. **Runs:** 2 per scenario per
  topology (12 reviews), `parallel` and `single`, interleaved. Starts on the laptop after its
  part of H ends.
- **Reported:** matches per topology (same scorer as H), and which budget stopped each review.
  No significance test (too few reviews); no claim from H2 alone.

### Configuration H2: cancelled

Not run: replaced by configuration I (01:55), which addresses the same "close but out of
budget" reviews more directly.

## Configuration I: `parallel` versus `single`, both with D6 (pre-registered 02:20)

Written and committed before the batch started; the results will be added below this paragraph
without changing it. Decided after seeing part of H, so it is a separate experiment: it never
changes H's verdict, and H's verdict never borrows from it.

- **Arms:** `parallel` (D5 + D6) and `single` (D1 + D6 best-attempt base and revision rule).
  Everything else as in H: model checkpoint, `serve_lightning.sh`, sampling (temperature 1.0,
  top_p 0.95, `THINKING_BUDGET=16384`), prompts, pre-checks, plan rules, RAG off.
- **Budget, identical for both and the same as H:** `max_revisions` 5, 40 calls, 450,000
  tokens, 3,000 s.
- **Infrastructure (differs from H):** a second Brev H100 (`defensive-green-cicada`, event
  credits) serves Lightning with the same script, and the runner (Saleor, Postgres) runs on that
  instance, reaching vLLM on 127.0.0.1. 8 workers.
- **Scenarios:** H's 9. **Runs:** up to 4 per scenario per topology (72 reviews), interleaved
  as in H (run index outermost). If the batch is stopped early, the analysis uses every
  completed run index (18 reviews each, both arms equally), never a partial one.
- **Primary metric and decision rule:** as in H (matches to Mahek's expected outcomes;
  `parallel` better only with more matches and two-sided Fisher exact p < 0.05).
- **Secondary:** as in H, plus revisions better / same / worse than the lineage's best attempt
  (compare H), and reviews stopped by each budget.
- **Exclusions:** as in H.

**Status (02:20):** started 02:15 on `defensive-green-cicada` (batch
`runs/brev-batch-20261018-061500` on that instance) and stopped two minutes later, with no
review finished, because the instance was handed to a teammate for another experiment. No
results; the code (`6654bb0`, branch `feat/config-i`) is tested (348 passed) and unrun.

**Status (04:20):** restarted from scratch on a third Brev H100, `human-brown-mollusk`
(Hyperstack H100 PCIe, driver 570, 28 vCPU, 177 GB RAM, event credits; `defensive-green-cicada`
was deleted). Same procedure, code (`d343bb5`, same code as `6654bb0`), serving script, budgets,
scenarios and 8 workers; the runner again runs on the instance. The stopped 02:15 batch is not used.

**Status (06:30):** cancelled by Harsha during repetition 2: 28 of 72 reviews finished, and
the 8 reviews in flight were killed (recorded in the batch's `INFRA-NOTE`). As pre-registered,
the analysis uses only completed repetition 1 (18 reviews, 9 per arm). It is to be added.

## Configuration J: `roles` versus `single`, with a schema/data profile (pre-registered 06:45)

Written and committed before the batch started; the results will be added below this paragraph
without changing it. A new experiment, decided after H and part of I. It never changes their
verdicts.

- **Why:** the prompts named the table and columns but never showed the live table or its
  data. A reviewer would look at both. The only signal of, for example, one note over 255
  characters or one duplicate last name was a runner failure after an attempt.
- **Arms:** `roles` (investigator, planner, tester, one planner per round) and `single` (D1).
  Both use I's D6 (revise from the best attempt, keep unimplicated stages) and two additions:
  - **Schema/data profile** (`MIGPROTO_SCHEMA_PROFILE=1`, `migproto/profile.py`): measured
    read-only at S0 on a throwaway clone (seed template plus the scenario's `prepare_sql`). It
    is the same generic profile for every scenario: the table's row count, and for each
    existing column the change names, its type, nullability, default, NULL count, distinct
    non-NULL count, longest text value, constraints and indexes. Numbers only, no
    interpretation. It is appended to the scenario text that every role (and `single`)
    receives, and is recorded in `agents/schema_profile.json`.
  - **Repeated-failure notice** (`MIGPROTO_REPEAT_NOTICE=1`): when the latest attempt fails a
    check, or fails with error text identical to the attempt before it, the revision feedback
    lists them and asks for a different targeted edit.
- **Everything else as in H/I:** model checkpoint, `serve_lightning.sh` (server reused on
  `human-brown-mollusk`), sampling (temperature 1.0, top_p 0.95, `THINKING_BUDGET=16384`),
  prompts, pre-checks, plan rules, RAG off. Budget identical for both: `max_revisions` 5,
  40 calls, 450,000 tokens, 3,000 s. 8 workers; the runner runs on the instance.
- **Scenarios:** H's 9. **Runs:** 2 per scenario per topology (36 reviews), interleaved, run
  index outermost. If the batch is stopped early, only completed run indexes are used.
- **Primary metric and decision rule:** as in H. `roles` is reported as better only with more
  matches and two-sided Fisher exact p < 0.05. With 18 reviews per arm, only a large
  difference can reach significance, so a null result here says little.
- **Secondary:** as in I. A comparison with H/I is descriptive only, because the
  configurations and topologies differ.
- **Exclusions:** as in H (only runs recorded in the batch's `INFRA-NOTE`).

### Configuration J: cancelled (06:50)

Cancelled about 12 minutes after it started (batch `brev-batch-20261018-103841`), before any
review finished, so it has no results. It was replaced by K (J plus the pre-execution dry run
and write probe below), on the same instance.

## Configuration K: `roles` versus `single`, J plus a pre-execution dry run (pre-registered 06:50)

Written and committed before the batch started; the results will be added below this paragraph
without changing it. A new experiment, decided after H, part of I, and the failure counts
below. It never changes their verdicts.

- **Why:** of H's 241 failed runner attempts, 37 halted because a plan SQL stage failed to
  execute and 36 because the deploy_new Django migration failed. A dry run of the plan
  surfaces both kinds of failure in seconds. Another 116 failed cross-version cases, mostly
  write sync between the two columns, which a scripted-write probe of the plan's triggers
  shows directly.
- **Arms:** `roles` and `single`, exactly as in J (D6, schema/data profile, repeated-failure
  notice), plus:
  - **Pre-execution dry run** (`MIGPROTO_PREFLIGHT=1`, `migproto/preflight.py`). Before every
    runner execution, the controller runs the plan on a throwaway clone of S0 (seed template
    plus `prepare_sql`). It executes the SQL stages in order and the deploy_new stage's
    Django migration, without application servers, and records per stage whether it executed
    and the first error.
  - **Write probe:** just before deploy_new, and again just after its migration, it runs one
    scripted single-column `UPDATE` on one existing row for each named text column. Each
    write is rolled back. It then reads back every named column. The values are reported
    with no judgement. INSERTs, ORM writes (which also write unchanged fields), concurrency
    and every runner check are not covered.
  - **One planner fix per attempt:** if the dry run hit an error, or the probe ran any
    write, the planner (or `single`) gets the report with its plan. It returns the plan
    unchanged or fixed, and the runner executes the result. An invalid fix falls back to the
    unfixed plan. The report is in `agents/preflight-<attempt>.json`. The extra calls count
    against the same budget, and the dry run's time (about 7-15 s per attempt) counts
    against the wall cap.
- **Everything else as in J:** model, server reused on `human-brown-mollusk`, sampling,
  prompts, pre-checks, budget (`max_revisions` 5, 40 calls, 450,000 tokens, 3,000 s), 8
  workers, H's 9 scenarios, 2 runs per scenario per topology (36 reviews), run index
  outermost. If the batch is stopped early, only completed run indexes are used.
- **Primary metric and decision rule:** as in H. `roles` is reported as better only with more
  matches and two-sided Fisher exact p < 0.05. With 18 reviews per arm, a null result says
  little.
- **Secondary:** per-scenario matches, attempts per review, how often the dry run found an
  error, and whether the fixed plan then passed the matching runner stage. A comparison with
  H/I is descriptive only.
- **Exclusions:** as in H.

### Configuration K: known defect during the batch (07:10)

Run `20261018-104946` (raw-sql-rename, `roles`) ended in ERROR before planner-1. The
investigator returned its findings as bare strings, and `roles.plan` crashed reading them
(`'str' object has no attribute 'get'`). The bug was already in the code before K, so H, I
and K all ran with it. The guard is committed on `feat/schema-profile` but **not** deployed to
the running batch, so every K review runs the same code. The run stays in K as a recorded
ERROR (a non-match). This is not an infrastructure exclusion.

## Configuration K0: K without the dry run, ablation (pre-registered 07:20)

Written and committed while K was running and before any K result was analysed. The results
will be added below this paragraph without changing it.

- **Question:** does the pre-execution dry run (`MIGPROTO_PREFLIGHT=1`) raise the match rate?
- **Arms:** identical to K, with `MIGPROTO_PREFLIGHT` unset. The schema/data profile, the
  repeated-failure notice and D6 stay on. This is J's configuration.
- **Code:** the same commit as K (`fc0aed7` on `human-brown-mollusk`). The later planner fix
  for malformed findings (`22144b8`) is deliberately not deployed, so the flag is the only
  difference.
- **Everything else as in K:** server, sampling, budgets, 8 workers, H's 9 scenarios, 2 runs
  per scenario per topology (36 reviews), run index outermost. It starts automatically when
  K's supervisor exits, so the two batches never share the GPU. If it is stopped early (for
  example at the 11:00 demo), only completed run indexes are used, and K is compared over the
  same run indexes.
- **Primary comparison:** matches with the dry run (K) versus without (K0), pooled over both
  topologies (36 versus 36), two-sided Fisher exact test. "The dry run improves accuracy" is
  claimed only if K has more matches and p < 0.05. Per-topology counts are secondary.
- **Secondary:** runner attempts per review, attempts halted by an SQL or migration error,
  tokens and wall time per matched review.
- **Caveat:** the batches run one after the other, not interleaved, so a drift in serving
  conditions over time would be confounded with the flag. The server is the same, and its
  serving record is kept in each batch directory.

## Configuration KB: one big model (gpt-oss-120b, `single`) versus K's Lightning `roles` (pre-registered 07:50)

Written and committed before the baseline batch started; the results will be added below this
paragraph without changing it.

- **Question:** do several Lightning agents (`roles`, 30B total parameters) match more
  scenarios than one large model working alone, with the same tools and budget?
- **Baseline arm (new):** `single` topology served by `openai/gpt-oss-120b` (revision
  `b5c939de`, about 117B total parameters, MXFP4) on vLLM 0.27.1 (`scripts/brev/serve_gptoss.sh`,
  reasoning parser `openai_gptoss`), on a separate 1x H100 instance (`extra-sapphire-baboon`,
  event credits). This is a comparison baseline only. It is not part of the submitted system,
  and it is outside the 33B budget.
- **Comparison arm:** K's `roles` reviews (Lightning on `human-brown-mollusk`), unchanged.
- **Same as K:** code commit `fc0aed7`, the flags `MIGPROTO_SCHEMA_PROFILE=1`,
  `MIGPROTO_REPEAT_NOTICE=1` and `MIGPROTO_PREFLIGHT=1`, sampling (temperature 1.0, top_p
  0.95), the `thinking_token_budget` of 16384 if vLLM accepts it for gpt-oss (recorded either
  way), prompts, and budgets (`max_revisions` 5, 40 calls, 450,000 tokens, 3,000 s). Also 8
  workers with the runner on the instance, H's 9 scenarios, and 2 runs per scenario (18
  reviews), run index outermost. If stopped early, only completed run indexes are compared,
  over the same run indexes of K.
- **Primary metric and decision rule:** matched reviews (as in H), K `roles` versus KB
  baseline, 18 versus 18, two-sided Fisher exact test. Either side is reported as better only
  with p < 0.05. With 18 per arm, p < 0.05 needs a gap of roughly 9 versus 2, so we expect
  to report the counts descriptively.
- **Secondary:** K `single` (Lightning) versus KB, per-scenario matches, tokens and wall time
  per matched review.
- **Confounds, disclosed:**
  - Different instance hardware (same instance type), and the batches overlap in time but
    are not interleaved on one server.
  - The model's reasoning format differs (harmony), so token counts are not like for like.
  - "Same budget" means the same token and call caps, not the same compute.

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

**Sweep 2** (`runs/brev-batch-20261017-212229`, with the variant guard): it
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

- **Run locations:** the sweep-2 and configuration D batches ran in the `hackathon-variant` and `hackathon-configd` worktrees; their `runs/` entries were copied unchanged (`cp -a`) into the main checkout's `runs/`, which is where every path in this document points.

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
