# Agent and Serving Results: Nemotron 3.5 Lightning on vLLM (Brev)

DRAFT, 2026-10-17 21:00 EDT. Configuration B and the scenario sweep are still running.

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
| Parallelism | up to 8 reviews at once on one vLLM server (separate app ports, locks, Redis DBs and run databases per worker); each review is still sequential internally |

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

## Configuration B: rename, full feedback (PENDING)

Batch `runs/brev-batch-20261017-204244`. Settings are A's plus compact, complete runner
feedback, with the tester summary first. Traced in Langfuse (environment `brev`).

- First result: `single` INCOMPLETE again (309k tokens; 250k prompt). Compact feedback alone
  does not fix `single`'s history growth.
- Remaining results pending.

## Scenario sweep (PENDING)

Batch `runs/brev-batch-20261017-204405`: one `roles` review on each of 10 catalogue scenarios,
with configuration B settings.

- **Scoring:** `scripts/brev/score_scenarios.py`. It compares the runner's verdict and
  recommendation for the agent's final plan with the best outcome in Mahek's manual expected
  verdicts (`proposals/manual/scenarios/`), which the agents never see.
- **Prompts:** `tests/agents/test_prompt_isolation.py` checks that no prompt contains scripted
  plans or expected verdicts.

Results so far (4 of 10):

| Scenario | Runner verdict | Recommendation (agent / runner) | Attempts | Tokens | Matches expected |
|---|---|---|---|---|---|
| add-optional-column (D1) | PASS | accept_as_is / accept_as_is | 1 | 18,865 | yes |
| add-table (D2) | PASS | accept_as_is / accept_as_is | 1 | 20,241 | yes |
| lengthen-varchar (D5) | PASS | accept_as_is / accept_as_is | 1 | 38,014 | yes |
| add-required-column (A4) | PASS | staged_rollout / staged_rollout | 1 | 39,570 | yes |

## Disclosures

- **Prompt content added during development.** None of it states a solution:
  - the runner-contract lines (SQL is sent verbatim; repeated statements must stop matching
    rows);
  - for scenarios other than the rename, a general Django fact: a column the running model
    does not know gets its database DEFAULT or NULL on INSERT. This bears on A4.
- **A4 plan:** the plan keeps the column's database default and uses `SELECT 1;` as its
  contract stage. The runner passed it, but that stage satisfies the "at least one contract
  stage" rule without doing anything. Mahek's reference plan drops the default instead.
- **Sample size:** one review per scenario and 5-7 per topology on the rename. These are counts
  under the stated conditions, not rates with confidence intervals.
