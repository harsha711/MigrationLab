# Assignment 02: Agents and Orchestration (Owner 2)

## Goal

Turn the existing investigator, planner and tester loop into a budgeted, fully recorded controller that runs on an eligible small model. It must support both one agent and cooperating roles under equivalent conditions, so that a fair comparison becomes possible.

## Current status (at `feasibility-v1`)

- `migproto/agents/loop.py::run(log, source)` runs investigate → plan → propose tests → `staged.run` → report evidence, with at most one revision (`MAX_REVISIONS = 1`).
- `migproto/agents/roles.py` holds the prompts (`SCENARIO`, `PLAN_CONTRACT`), the deterministic search tool `gather_hits` (35 hits for `note`), and `score_investigator` against `proposals/manual/investigator_ground_truth.json`.
- `migproto/agents/llm.py` is an OpenAI-compatible client configured by `MIGPROTO_LLM_BASE_URL`, `MIGPROTO_LLM_MODEL` and `MIGPROTO_LLM_API_KEY`. It retries invalid JSON up to 2 times, and each retry is recorded in the transcript.
- Recorded today: `agent_config` event (model, base URL, max revisions), `agents/<role>.json` proposals, `agents/transcript-<role>.json` (messages, response, seconds, usage). Token usage is not summed or budgeted.
- Only the roles topology exists. There is no single-agent mode, no review request input, and no `incomplete` outcome.
- Scripted mode (`--proposals scripted`) reads `proposals/scripted/`. It exercises the plumbing only, and its investigator copies the ground truth.
- The single live run used `gpt-oss:120b-cloud`. That is a development smoke test, **not** the qualifying small-model run.

## Owned paths

`migproto/agents/` (`llm.py`, `roles.py`, `loop.py`, plus new files such as `controller.py`, `single.py`, `budget.py`), `proposals/scripted/`, new `tests/agents/`. The `agent-loop` subparser in `migproto/cli.py` is shared: add flags there, and Harsha reviews.

## Ordered tasks

1. **Baseline.** Run `uv run migproto agent-loop --proposals scripted` and read `examples/live-ordering-revision/` (summary, `agents/`, `model_calls.json`) to see what a live run records.
2. **Review request input.** Accept the Review request contract (`docs/contracts.md`, section 1) as a JSON file (`--request path`). Default to the current scenario so the existing command keeps working. Build `SCENARIO` from the request instead of the constant.
3. **Small-model endpoint.** Run against a configurable eligible small model through the existing env vars. Record the model id, endpoint, temperature and any server-reported model metadata in `agent_config`. Do not hard-code a provider.
4. **Budgets and outcomes.** Add a per-review budget (`max_total_tokens`, `max_model_calls`, `max_wall_seconds`, `max_revisions`) enforced in the client wrapper. On exhaustion, invalid output after retries, or endpoint failure, finish with verdict `INCOMPLETE` or `ERROR` and a reason. Never let a missing proposal look like a pass.
5. **Recording.** For every attempt, emit events for: the proposal (plan JSON and generated SQL), selected tests, feedback sent, revision notes, per-call latency and token usage (when the endpoint reports it), and totals in `summary.json`. Mark `solution_origin` as `generated`, `selected` (chosen from provided candidates) or `scripted`.
6. **Structured claims.** Emit Agent claim records (`docs/contracts.md`, section 5) that cite observation IDs. Claims never carry pass/fail.
7. **Single-agent topology.** Add `--topology single|roles`. The single agent gets the same tools (`gather_hits`, catalogue view, runner execution through `runner_api`), the same evidence access, the same revision limit and the same total budget as the roles topology combined.
8. **Scoped retrieval.** Read prior observations through Owner 3's query API once it exists. Until then, use an adapter over `examples/` with the same function signature.
9. **Comparison harness (integration step 6).** Repeated runs per topology on the main scenario and Harsha's held-out variation. Report per-run outcomes, attempts, tokens and latency. State no conclusion beyond the measured runs.

## Bounty work: Nemotron 3.5 Lightning + Red Hat vLLM

Eligibility and requirements are in `docs/bounties.md`. Track requirements are **not yet verified**, so do not claim qualification. Model accounting is in `docs/model-inventory.md`. This is priority 1 for the team.

Ordered bounty tasks. These replace task 3 above and feed tasks 4-9.

1. **Remove the 120B default.** In `migproto/agents/llm.py`, require `MIGPROTO_LLM_MODEL` to be set explicitly for `--proposals llm`, or default it to the served Lightning name. `gpt-oss:120b-cloud` stays out of the submitted workflow. Its runs are labeled development-only.
2. **Confirm compatibility before provisioning.** Lightning NVFP4 needs vLLM ≥ 0.27.1 and Hopper or Blackwell (or Ampere through W4A16). Weights are about 18 GB. Provision an H100-class instance with the **event-provided NVIDIA Brev credits** only, never personal machines, accounts or credits.
3. **One shared vLLM deployment.** Serve `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` at the pinned revision with the no-speculative-decoding H100 recipe in `docs/bounties.md`, section 5 (no draft model, which keeps the parameter budget clear). All roles and the single-agent mode call this one endpoint.
4. **Record serving facts.** Write `docs/serving.md` with the exact command, `vllm --version` or image digest, checkpoint and revision, flags, GPU model, driver and CUDA version, Brev instance type, and startup log excerpt. Mirror the key fields into each run's `agent_config` event (`model`, `revision`, `serving_backend`, `serving_version`, `hardware`).
5. **Separate role contexts.** Each role call starts from its own system prompt and only the inputs that role is given. No shared chat history between roles. The single agent gets one context with the same tools and evidence.
6. **Structured outputs.** Use vLLM structured outputs (`response_format` with a JSON schema derived from `plans.py`) where the server supports it, and keep validating every response with `plans.validate_plan` / `validate_tests` in code. Handle the reasoning parser: confirm `message.content` is non-empty (the model card suggests `chat_template_kwargs.force_nonempty_content`), and record whether thinking is enabled and which temperature and top_p were used (the card recommends 1.0 / 0.95, while `llm.py` sends 0).
7. **Measure.** Per run and per topology: valid-proposal rate (schema-valid responses / model calls), per-call latency, prompt and completion tokens, attempts used, final plan success. Write the measured table to `docs/results.md` from saved runs. Promote representative runs with `scripts/export_example.py`.
8. **Compare topologies.** Single agent versus cooperating roles: same Lightning deployment, tools, evidence, revision limit and total budget, same frozen tasks, repeated runs. Report the numbers without a superiority claim beyond them.
9. **Budget check.** Before enabling MTP, DSpark, DFlash, or any extra model, update `docs/model-inventory.md` and get the organizer accounting answer (`docs/bounties.md`, question 2).
10. **Only after the core loop works:** prefix-caching and concurrency experiments (priority 4), recorded in `docs/serving.md`.

Additional owned paths: `docs/serving.md`, `docs/results.md`, `docs/model-inventory.md` (Harsha reviews).

Bounty acceptance: one Lightning-on-vLLM run of each topology completes through the existing loop with a recorded model, revision, serving version, hardware, budget, per-call usage, and runner verdict. The scripted fallback still works without a GPU.

## Inputs and outputs

- Inputs: Review request, `runner_api.execute_plan` results (Harsha), memory query API (Owner 3), model endpoint.
- Outputs: candidate plans and tester proposals (validated by `plans.py`), agent claims, agent events, totals in `summary.json`.

## Dependencies

- Harsha: `runner_api.execute_plan`, complete feedback fields (G5), held-out scenario flag. Until `runner_api` lands, keep calling `staged.run`.
- Owner 3: scoped query API. Until then, use fixtures.
- Changing `loop._feedback` together with Harsha's G5 fix: agree on the field list first.

## Acceptance criteria

- `uv run migproto agent-loop --proposals scripted` still exits 0 and still shows attempt 1 failing and attempt 2 passing.
- A live small-model run produces `agent_config` with model identity and budget, per-call usage (or an explicit `null` when not reported), totals, all attempts including failed ones, and claims with evidence references.
- Budget exhaustion yields `INCOMPLETE`, not `FAIL` or `PASS`. Endpoint failure yields `ERROR`.
- `--topology single` and `--topology roles` run against the same request with identical budgets and mandatory checks (assert this in `tests/agents/`).
- No prompt, tool or file access exposes `proposals/scripted/plan_v2.json`, `proposals/manual/`, `patches/`, `heldout/` or `examples/` outcomes to the evaluated model. Add a test that greps rendered prompts for these paths and for known solution strings such as `migproto_note_sync`.

Verification commands:

```bash
uv run pytest -q
uv run migproto agent-loop --proposals scripted
MIGPROTO_LLM_BASE_URL=... MIGPROTO_LLM_MODEL=... uv run migproto agent-loop --proposals llm
```

## Out of scope

Rewriting `staged.py`, `checks.py` or `db.py`. Weakening, skipping or reordering mandatory checks. Deciding pass/fail in agent code. UI and SQLite storage. Claiming multi-agent superiority from a single run or from the 120B smoke test.

## Suggested branch

`agents/small-model-loop`

## Starting prompt for your coding LLM

```
You are working in the migproto repository root, role: Owner 2, agents and
orchestration. Read AGENTS.md, docs/contracts.md and docs/team/02-agents.md first. Work only in
migproto/agents/, proposals/scripted/, tests/agents/ and the agent-loop flags in
migproto/cli.py. Do not modify staged.py, checks.py, db.py or plans.py; propose runner changes
to Harsha in a handoff note.

Priority 1 is the bounty section of docs/team/02-agents.md: remove the gpt-oss:120b-cloud
default, then make the existing loop run against one shared vLLM deployment of
nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 (served on event-provided NVIDIA Brev
credits only, never personal machines or accounts), with structured outputs validated by
plans.py and model/serving/hardware recorded in agent_config. Read docs/bounties.md and
docs/model-inventory.md; do not add any model (including draft models) without updating the
inventory. Do not claim bounty qualification. Then do tasks 2 and 4: review request JSON input
and a total budget that ends a review as INCOMPLETE instead of PASS/FAIL. Keep pass/fail decisions
in the runner. Never put proposals/scripted, proposals/manual, patches/ or examples/ outcomes
into prompts.

After each change run: uv run pytest -q and uv run migproto agent-loop --proposals scripted.
Do not rerun the 120B model just to test plumbing; use scripted mode or a small model.
```
