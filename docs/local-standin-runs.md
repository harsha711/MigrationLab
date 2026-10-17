# Local Stand-in Runs (not Nemotron, not vLLM)

These runs exercise the agent controller, budgets and runner integration on a laptop GPU with a
small local model served by Ollama. They are development runs. They are **not** Nemotron 3.5
Lightning results, they were not served by vLLM, and they stay out of `docs/results.md`.
They say nothing about topology quality. Each row is one run at temperature 1.0.

## Setup

| Field | Value |
|---|---|
| Model | `qwen3:4b-instruct` (Ollama digest `0edcdef34593`, Q4_K_M, non-thinking) unless noted |
| Context | Ollama alias with `num_ctx` 16384 or 24576 (Ollama's default 4096 truncates prompts silently) |
| Serving | Ollama 0.32.1, OpenAI-compatible `/v1/chat/completions` on `localhost:11434` |
| Hardware | RTX 3070 Ti Laptop 8 GB, 115 W limit, driver 580.173.02 |
| Sampling | temperature 1.0, top_p 0.95 (controller defaults) |
| Budget | 1 revision, 10 calls, 40,000 tokens; wall 600 s (default) or 1800 s (`local-standin-wall1800` request) |
| Scenario | default request: `User.note` to `customer_note` |

## Runs

| Run | Topology | Context / timeout | Verdict | Calls (valid) | Tokens | Wall | Where it stopped |
|---|---|---|---|---|---|---|---|
| `20261017-151551` | roles | 16k / 600 s, `qwen3:4b` thinking | INCOMPLETE (wall, 600 s) | 2 (n/a) | 25,011 | 883 s | Thinking calls ran several minutes each; wall budget overran by 283 s because it was checked only between calls |
| `20261017-154618` | roles | 16k / 600 s | FAIL | 7 | 20,183 | 118 s | Attempt 1 refused by `retirement_gate`; attempt 2 had 18 check failures, contract skipped |
| `20261017-154903` | single | 24k / 600 s | INCOMPLETE (wall, 1800 s) | 7 (4) | 34,827 | 1,971 s | Attempt 1 wrote `customer_note` before adding it; revision call timed out three times (600 s each) |
| `20261017-163315` | roles | 24k / 120 s | FAIL | 8 (7) | 25,722 | 433 s | Attempt 1 refused by `retirement_gate` (contract before `retire_old`). Revision fixed the ordering, but attempt 2 used a one-time backfill with no sync; 15 of 28 checks failed and the contract was skipped |
| `20261017-164455` | single | 24k / 120 s | INCOMPLETE (tokens, 43,397) | 6 (4) | 43,397 | 308 s | Attempt 1 wrote `customer_note` before adding it; one revision call timed out, the retry returned a plan with an invalid stage id and went over the token budget |

Run directories are under `runs/` (git-ignored). No run produced a passing plan.

## What these runs show

- The controller, both topologies, schema validation, the budgets and the runner work end to end
  with a live OpenAI-compatible server. Failed attempts are kept in `summary.json`.
- The runner refused every unsafe plan: early contract stages (`retirement_gate`), stages that
  failed to execute, and contracts after failed cross-version checks.
- The 4B stand-in does not know that a rename needs bidirectional sync while both app versions
  run. That is a model capability question for Lightning, not something these runs can answer.
- `single` resends its growing history. Its prompt reached 12.2k tokens by the revision call,
  compared with at most 3.7k for any `roles` call, so under an equal total-token budget it runs
  out first. Decide the fairness rule (equal tokens, equal calls, or report both) before the
  Brev measurements.

## Fixes made from these runs

Commit `c7c7fe5`:

- `MIGPROTO_LLM_TIMEOUT` sets the per-call timeout and is recorded as `request_timeout_s`.
- Each request timeout is capped by the remaining wall budget.
- A retry after a request failure resends the same messages.
- Ollama's `reasoning` field is recorded when `reasoning_content` is absent.

## Known gaps

- Ollama ignores `chat_template_kwargs` and its `completion_tokens` exclude thinking tokens, so
  token budgets are not comparable to vLLM runs.
- `INCOMPLETE` exits with code 1, the same as `FAIL`. Changing it is a shared CLI change for
  the runner owner.
