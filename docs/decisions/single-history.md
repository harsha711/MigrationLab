# Decision record: single-agent history, invalid revisions, scenario search

Status: decided by Harsha on 2026-10-17 (branch `feat/single-history`, based on
`brev/lightning-serving` at `b9009bf`). The code is in `migproto/agents/single.py`, `loop.py`,
`roles.gather_hits` and `facts.old_version`; the tests are in
`tests/agents/test_single_history.py`. If you want to change one of these, record the new
decision here instead of silently reverting it.

## Why

On Nemotron 3.5 Lightning, all 7 `single` reviews ended `INCOMPLETE` at the 300k-token
budget. About 250k of those tokens were prompt tokens: every request resent the whole
conversation, and every request already restates the scenario, the plan contract and the
full plan or evidence. `roles` reviews used 136-190k tokens in total.

## D1. Compacted memory for `single` (policy `latest-plan-evidence/v1`)

The agent still has one memory. Before each request, the controller (`single.Memory.begin`)
rebuilds it as follows:

| Kept in full | Replaced |
|---|---|
| system prompt (`single.SYSTEM`, unchanged) | every other earlier exchange: older plans, all tester proposals, older evidence reports |
| first user turn (investigator request with search hits) **and the investigator's reply** | |
| the most recent planner exchange (its request carries the previous plan and the runner feedback) | |
| the most recent tester-report exchange (the full runner evidence for the latest attempt) | |
| the current request | |

- **The note:** the replaced exchanges become one deterministic note, built by the controller only from runner results: the replaced turn labels, then per attempt the attempt number, `plan_id`, PASS/FAIL, the runner halt reason (first 300 characters) and the failed check names. A model never writes or summarises anything in it. It is prefixed to the first kept user turn after the investigator exchange, so the messages keep alternating user/assistant. That avoids a chat template rejecting two user turns in a row; this rejection has not been verified on Lightning.
- **Why the investigator reply is kept:** it is the agent's own findings. Every planner request repeats the affected findings anyway, so the cost is small. (Decision: keep it, rather than dropping it as a strict reading of the brief would.)
- **On by default:** there is no flag. Every review records `history_compaction: "latest-plan-evidence/v1"` in its `agent_config` event and in its Langfuse trace metadata. Runs before this change have no such field, which is how full-history runs are told apart. Do not mix the two kinds in one results table without labelling them.
- **Audit:** every `transcript-single.json` call record gains `history_compaction`: `{policy, kept_turns, replaced_turns, messages_sent}`. `messages` in the same record is exactly what was sent.
- **Information parity:** the note's runner facts (failed check names and halt reasons of earlier attempts) are less than full history gave `single` before. They are still more than a `roles` planner sees, which is only the latest attempt. This was accepted as specified. Since configuration F, both topologies' planner feedback also carries `earlier_attempts` (runner facts and the plan of every earlier attempt, see D3), so the kept planner exchange grows by one record per attempt, capped by `roles.EVIDENCE_CHARS`.
- **`roles` is unaffected:** the rename-note `roles` prompts are byte-identical to `b9009bf`. This was checked by hashing all 13 requests of a fake 4-attempt review, with the run ID normalised. `single` requests 1-3 are also identical; compaction starts at request 4.
- **Replayed effect:** `runs/20261017-191414-agent-loop-llm` was recorded on Lightning and replayed through `Memory`; the estimate uses about 0.24 prompt tokens per character. The last request drops from 142.6k to 55.3k characters. Total prompt volume drops from 958k to 556k characters, which is about 232k to about 135k prompt tokens. This is an estimate from replay, not a measurement. Real runs would get different model replies.
- **Possible next step (not done):** the latest planner request and the latest report request both carry the same attempt's runner feedback, which is the main remaining cost (about 55k characters at the plateau). Dropping one of the two would need a new decision.

## D2. An invalid planner revision gives `FAIL`, not `ERROR`

If `planner-N` with N >= 2 raises `LLMError` (no valid reply after retries) or `PlanError`, the
review ends with `verdict: FAIL`, `ok: false`, the earlier attempts' runner results in
`attempts`, and the reason `revision invalid: <error>`. A `revision_invalid` event (agent
provenance) is logged.

- **Why:** the runner had already failed every plan it executed, so the review's outcome is a runner FAIL. An unusable revision is not an infrastructure failure.
- **Unchanged:**
  - An invalid first plan (`planner-1`) or an invalid tester proposal stays `ERROR`. (Decision: scope it to planner revisions only.)
  - `BudgetExceeded` during a revision stays `INCOMPLETE`.
  - A plain `ValueError`, meaning a controller bug, stays `ERROR`.

## D3. `gather_hits` searches the scenario's OLD app version

For scenarios with `old_version != "old"` (today only `raw-sql-rename`, `raw-sql-rename/old`),
the deterministic search runs in that scenario's old worktree. That build is the code under
review, deployed before the change. `facts.old_version()` refuses any version that is also a
new-version (`apply`, `state_only`, ...) build. The `gather_hits` tool event records
`app_version`.

- **Annotation filter:** scenario old builds are generated from `patches/<scenario>/old`, and their `# EXPERIMENT (manual) ...` comments explain the trap. `gather_hits` drops comment blocks that start with `EXPERIMENT` or `not upstream` from both hits and context (`roles._annotation_lines`). Today the raw-sql-rename comment is outside the ±2 context lines anyway; the filter keeps it out if the context widens. (Decision: filter in code rather than edit the patch. Regenerating vendor worktrees would touch the checkout the live batches use.)
- **Effect:** rename-note hits are identical (35). raw-sql-rename now includes `saleor/graphql/account/types.py:805` (`SELECT note FROM account_user ...`) with no annotation text.
- **Follow-up for the patch owner:** consider removing the `EXPERIMENT` comment from `patches/raw-sql-rename/old` itself.

## D3. Planner feedback: runner facts first, earlier attempts as facts, no controller ranking

Configuration F (2026-10-17, after configuration E). `loop._feedback` sends, in order: the
runner evidence of the latest attempt (`ok`, `halted`, `stages`, every failed check compacted),
then `tester_hypothesis` (the tester's report, labelled as an unverified model claim that loses
to the runner evidence), then `earlier_attempts` (for each earlier executed attempt: `halted`,
`halted_step`, `stages_run`, failed check names and the plan). `loop._fit` keeps the JSON under
`roles.EVIDENCE_CHARS` by dropping earlier plans (oldest first), then earlier attempts, then
trailing failed checks with a count, so the prompt slice never cuts the JSON.

- **Why not a "best attempt":** configuration E ranked attempts by passed runner checks. In run
  `20261017-220028` that pointed the planner at attempt 2 (20 passed, 18 failed) instead of
  attempt 1, which had the trigger in the right order and halted early on a backfill (few
  checks ran). Passed and failed counts are not comparable across attempts that halt at
  different stages, so the controller no longer ranks; the planner sees the facts.
- **Why the tester's report moved:** in the same run it was read ahead of runner evidence
  that contradicted it (`triggers: []` at `deploy_new`). It is still sent in full.
- **Backfill pre-check:** `facts.check_backfill_terminates` rejects a `repeat_until_zero_rows`
  plain column copy (`UPDATE t SET a = b ...`) whose WHERE the update does not make false
  (`WHERE a IS NULL` without `b IS NOT NULL`, `WHERE b IS NOT NULL`, no WHERE). On all 203
  recorded Lightning planner replies it flagged all 18 whose backfill hit the runner's
  10,000-batch stop, none of the 25 whose backfill converged, and 8 whose backfill never ran.
  It is agent-side, like the SQL quoting check; the runner still executes and judges.

- **SQL syntax pre-check (`fix/next`):** `facts.check_sql_syntax` parses every sql string with
  PostgreSQL's parser (`pglast` 8.6, libpg_query, PostgreSQL 18 grammar; the runner database is
  PostgreSQL 15) and every plpgsql function body and DO block with the PL/pgSQL parser. On the
  203 recorded Lightning planner replies it flagged all 18 whose stage failed with a syntax
  error, none of the 181 others whose stages ran, and 4 in stages that never ran; no plan under
  `proposals/` or `examples/` is rejected. It executes nothing and checks no names or types.
- **Pre-checks are advisory (`fix/next`):** the SQL quoting, syntax and backfill pre-checks raise
  `facts.PreCheckError` and run after every hard check. `chat_json` sends a miss back for
  correction while retries remain; if only a pre-check fails on the final retry, the reply is
  accepted (transcript `error` says "advisory pre-check not met"), the controller logs
  `precheck_not_met`, and the runner judges the plan. Reason: in configuration F, run
  `20261017-224014` ended ERROR because planner-1's three replies failed three different
  validations, the last one the backfill pre-check; that plan would otherwise have reached the
  runner. Hard errors (schema, variant, recommendation) still fail the call.

## D5. Topology `parallel`: independent planners, the runner chooses

`agent_topology: "parallel"` (`loop._run_parallel`, `PARALLEL_PLANNERS = 3`). Investigator as in
`roles`, then rounds:

1. **Plan:** three planner calls at once, one per lineage (a, b, c), each an isolated role call
   on the shared endpoint (vLLM batches them). Round 1 has no feedback; temperature 1.0 gives
   different plans. No fixed seeds, so repeated reviews stay independent samples.
2. **Test:** one tester proposal per valid plan, also at once. An invalid tester reply drops only
   the extra cases (logged `tester_invalid`); the mandatory runner checks still run.
3. **Execute:** the runner executes the valid plans one at a time, each as its own attempt (`a1`,
   `a2`, ...). The first runner PASS ends the review. Every executed attempt stays in
   `summary.json` with `round` and `planner`.
4. **Revise:** each planner revises its own last plan from its runner evidence, plus the runner
   facts and plans of the other planners' attempts in the same round (`earlier_attempts`,
   `author: "another planner, same round"`). An invalid reply keeps the lineage for the next
   round; a lineage with no executed plan continues from another's.

- **No tester report:** revisions get runner facts only. In configuration E (`220028`) a wrong
  tester diagnosis led a revision to move a correct trigger (D3). `roles` and `single` keep it.
- **No controller ranking:** the controller never picks a "best" plan (D3: ranking by passed
  checks pointed at a regression). Each planner sees all the facts and chooses.
- **Same budget:** the review budget (calls, tokens, wall) is shared by all threads and is the
  same for every topology in a comparison, so `parallel` buys breadth with the same tokens
  `single` spends on one growing history.
- **Why:** on Lightning, rename reviews got stuck repeating one wrong idea (F `225329`: the same
  refused trigger three times), and `single` ran out of tokens in 5 of the last 6 G/M reviews.
  Separate contexts keep each call small; three lineages explore in parallel.
- **Not changed:** runner checks, verdicts, the retirement gate, pre-checks and prompts.

## D6. Revise from the best attempt; majority-reject stop; budget-sized rounds (configuration I)

Three controller changes (`loop.REVISION_BASE = "best"`, `_revision_input`, `_affordable`):

- **Best-attempt base (all topologies):** the next revision's "Previous plan" is the lineage's
  executed attempt with the most progress, by runner facts only: most stages executed, then
  fewest failed checks (latest wins ties). An early halt never outranks a plan that got further
  (the run `220028` case that made D3 avoid a "best attempt"). The feedback names the base
  attempt, lists every other attempt as runner facts, and adds one rule: keep stages no failed
  check implicates, change only what the failed checks point to. The tester report is shown
  only when it describes the base. Logged as `revision_base` events.
- **Majority-reject stop (`parallel`):** a review stops when most executed plans in a round
  were planner-recommended `reject` and runner-rejected (D5 required all of them).
- **Budget-sized rounds (`parallel`):** from round 2, if the remaining tokens (at the review's
  average tokens per call) or calls cannot pay a planner and a tester call for every lineage,
  only the lineages that got furthest revise (`round_narrowed` event).
- **Why:** in configuration H's first 64 revisions, 32 were worse than the best plan the review
  already had, 15 the same and 17 better (rename-note single 1 -> 15 -> 17 -> 22); `parallel`
  ran unique-last-name to the token cap because one lineage kept revising; parallel rounds
  started with one call of budget left and overshot by up to 59k tokens.
- **Not changed:** runner checks, verdicts, the retirement gate, pre-checks, prompts other than
  the one revision rule, budgets.
