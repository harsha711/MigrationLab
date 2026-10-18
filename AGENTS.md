# AGENTS.md: migration reviewer (migproto)

Instructions for humans and coding agents working in this repository. Saleor's own `AGENTS.md` lives in `vendor/saleor/AGENTS.md` and applies to any code under `vendor/`.

## Project

A migration reviewer that generates and revises staged migration plans, executes them against old and new Saleor application versions running side by side, and records evidence scoped to each application version and database state. Saleor already tests migration compatibility upstream (`.github/workflows/test-migrations-compatibility.yml` applies a PR's migrations, then runs the base branch's tests). Our contribution is generating and revising staged plans from execution evidence gathered while old and new versions coexist.

## Current verified scope

Every statement below is backed by `REPORT.md` and by fixtures in `examples/`. Nothing beyond it is verified.

- **Scenario:** Saleor `User.note` renamed to `customer_note` (`account_user.note`), intentionally introduced. It is not an upstream bug. Saleor is pinned at `0c0e2697cc72aa999f7786ae8aeda7665bd4285f`.
- **Existing upstream tests:** 4 account test files, 110 of 110 passed on both `old` and `new-unsafe`. They were not run on `new-staged`, which is only correct together with plan SQL.
- **Immediate rename:** migration exit 0, new app works, but old-app read, write and create all fail with `column account_user.note does not exist`. Runner checks: 7 of 7 expected outcomes observed (`examples/unsafe-rename`).
- **Scripted one-way sync (plan v1, manually authored and deliberately flawed):** 9 of 29 checks failed (old writes never reached `customer_note`, 7 rows disagreed), and the runner refused the contract stage (`examples/scripted-sync-failure`).
- **Scripted staged alternative (plan v2, manually authored):** 33 of 33 documented checks passed (`examples/scripted-staged-success`). The pass covers only those checks: sequential interleaving, 245 rows, one machine.
- **Live run:** one `gpt-oss:120b-cloud` run (via Ollama, cloud-backed) generated its own SQL. Attempt 1 had 30 checks, of which 1 failed: `retirement_gate`, because the contract stage was placed before `retire_old`. After that feedback, attempt 2 reordered the stages and passed 41 of 41. The run made 8 model calls, used 30,036 total tokens, and took 88.2 s (`examples/live-ordering-revision`).
- **Status of the 120B run:** it is a development smoke test. It is **not** our qualifying small-model implementation, and it sits outside the 33B parameter budget. Nemotron 3.5 Lightning has been served on vLLM 0.27.1 on a Brev H100 (2026-10-17) and measured in small batches (configurations A-F, `docs/results.md`). On the rename scenario, 3 `roles` reviews passed across A-E (`190133`, `204305`, `214321`, each a few reviews per configuration, not a rate); no `single` review passed. In the scenario sweeps, 10 of the 13 catalogue scenarios matched the expected outcome in at least one review. A NeMo Gym resources server exists in `gym/` (implemented, not run against a NeMo Gym trainer; only scripted-plan smoke runs, see `gym/README.md`).
- **Multi-agent claim:** no superiority over a single agent has been established. In the pre-registered equal-budget comparisons on Lightning (H's 9 scenarios, two-sided Fisher exact test), there is no significant difference in any batch:
  - H: `parallel` 21/36 vs `single` 19/36, p = 0.81.
  - K: `roles` 11/18 vs `single` 11/18, p = 1.0. K includes the schema/data profile, the repeated-failure notice and the pre-execution dry run.

  Descriptively, in K `roles` used about half the tokens per matched review (156k vs 291k). A comparison with one big model has not been measured (KB was not run). See `docs/results.md`. Local 4B stand-in runs are separate and measure nothing about Lightning (`docs/local-standin-runs.md`).
- **Tests:** `uv run pytest` runs 360 tests (counts per folder below are from before configurations J/K) on Linux with `uv` / Python 3.12: 85 runner tests (SQL guard, plan schema, trigger semantics on a scratch table, runner v1), 109 in `tests/agents`, 81 in `tests/memory` and 64 in `tests/gym`.
- **Evidence store and review UI (Avritti, merged through `integrate/memory-ui`):**
  - **Fixtures:** ingesting the four `examples/` fixtures reproduces their `summary.json` facts. For `scripted-sync-failure` at `deploy_new`, every old-writer/new-reader case fails and every new-writer/old-reader case passes.
  - **v1 records and Mahek's controller output** (structured claims, plan lineage, review request, budgets): unit tests use synthetic fixtures in `tests/memory/fixtures/`. Real local runs were also ingested by hand: in scripted run `20261017-165715` all 64 checks carry v1 scope across attempts `a1` and `a2`, and the tester claim's 9 evidence IDs all resolve.
  - **Run Review** has been started from the UI once end-to-end: `#/review`, `--proposals scripted` (no model), topology `roles`, through `agents.loop.run_review` under a real lock file. Run `20261017-213756-agent-loop-scripted` was PASS (65.2 s, 64 checks; attempt 1 failed 9 checks, attempt 2 failed 0). LLM mode from the UI has not been exercised. See `docs/ui.md`.
  - **`memory.retrieval.prior_evidence`** exists, but the controller does not call it yet. It takes a required `model` and selects only runs whose provenance is exactly `agent:<model>`, plus an optional exact `scenario` filter (unit-tested on synthetic run directories only).

## Architecture

| Path | Responsibility | Owner |
|---|---|---|
| `migproto/cli.py`, `migproto/__main__.py` | argparse CLI: `setup`, `baseline`, `unsafe`, `staged`, `agent-loop`, `all`. Wraps each command in a `RunLog` | Harsha |
| `migproto/config.py` | pinned commit, worktree paths, ports, DB settings, seed size | Harsha |
| `migproto/runlog.py` | `RunLog`: `runs/<run_id>/events.jsonl`, `check()`, `write()`, `summary.json`, provenance constants | Harsha |
| `migproto/db.py` | disposable DB lifecycle (template clone and drop), `guard_target`, `guard_sql` denylist, `run_guarded`, schema introspection | Harsha |
| `migproto/setup_env.py` | Docker services, pinned shallow clone, worktrees plus `git am patches/<version>`, Saleor venv sync | Harsha |
| `migproto/saleor_app.py` | `manage()` for `manage.py` calls, `AppServer` (uvicorn per version, GraphQL token, `read_note` / `write_note` / `create_customer`) | Harsha |
| `migproto/seed.py` | deterministic synthetic notes, seed script, row snapshots and checksums | Harsha |
| `migproto/baseline.py` | seed template build, old-app smoke checks, upstream pytest subset | Harsha |
| `migproto/unsafe.py` | immediate-rename reproduction and evidence | Harsha |
| `migproto/plans.py` | plan and tester-proposal schema validation | Harsha |
| `migproto/checks.py` | manual check catalogue, expected-value map, `run_case`, preservation, column agreement | Harsha |
| `migproto/staged.py` | executes plan stages, retirement gate, per-stage checks, contract-skip on earlier failures | Harsha |
| `migproto/agents/llm.py` | OpenAI-compatible chat client (plain `requests`, JSON replies) | Mahek |
| `migproto/agents/roles.py` | investigator, planner, tester prompts. `gather_hits` search tool, investigator scoring | Mahek |
| `migproto/agents/loop.py` | controller: investigate, plan, propose tests, execute, revise at most once | Mahek |
| `proposals/scripted/` | scripted proposals that run the loop without a model | Mahek |
| `proposals/manual/`, `proposals/negative/`, `patches/` | answer labels, negative fixtures, reference app patches | Harsha |
| `tests/` | runner tests | Harsha (new agent and memory tests go in `tests/agents/`, `tests/memory/`) |
| `scripts/export_example.py`, `examples/` | sanitized fixture export and frozen fixtures | Harsha |
| `docs/contracts.md` | shared data contracts and gap register | Harsha (coordinator) |
| `migproto/memory/adapter.py`, `store.py`, `query.py` | run directory to scoped records (v0 derivation rules, v1 pass-through, Mahek claims), append-only SQLite store `data/memory.sqlite`, exact-scope queries and compatibility matrix | Avritti |
| `migproto/memory/retrieval.py` | `prior_evidence(change, model=, scenario=)` for the controller: earlier agent runs of the same model and change only, never `examples/` or scripted runs | Avritti (Mahek wires the call) |
| `migproto/ui/server.py`, `review.py`, `static/` | `migproto ui`: read-only Replay pages, plus Run Review through `agents.controller.run_review` under `migproto.lock` (lazy, disabled until both exist) | Avritti |
| `gym/` | NeMo Gym resources server wrapping the runner (`gym/resources_server/`, nemo-gym 0.7.0), frozen tasks (`gym/tasks/`), runner-backed verify (`gym/migration_review/`). Implemented, not run against a NeMo Gym trainer; see `gym/README.md` | Harsha |
| `docs/serving.md`, `docs/results.md` (planned), `docs/model-inventory.md` | vLLM serving record, measured results, model and parameter inventory | Mahek |
| `docs/bounties.md` | bounty requirements (verified versus unverified), owners, status, open questions | Harsha (coordinator) |

Data flow: `migproto/agents/loop.py` asks roles for proposals. It passes plan and tests to `staged.run`, which starts `old` and `new-staged` servers on a fresh `migproto_run_*` clone of the `migproto_seed` template. The runner executes SQL stages through `db.run_guarded` and records every observation through `RunLog`. The memory adapter then reads finished run directories, meaning those with `summary.json`, into the SQLite store. The UI and `memory.retrieval` only read from that store. They never write observations.

### Agent-layer execution model (Mahek)

Nemotron is the reasoning model, vLLM is the inference-serving layer, and `migproto/agents/loop.py` is the orchestrator. Do not describe Nemotron or vLLM as the orchestrator, and do not move runner authority into the agent layer.

One review currently runs sequentially because every step consumes the previous step's artifact:

```text
review request
  -> investigator findings
  -> planner plan
  -> tester proposals
  -> deterministic runner observations
  -> tester evidence summary
  -> optional planner revision
  -> tester proposals
  -> deterministic runner verdict
```

Design decisions that other agents must not silently revert (single-agent history compaction, invalid revisions as FAIL, scenario old-version search) are recorded in `docs/decisions/single-history.md`.

Roles do not share chat history or communicate directly. The controller passes explicit JSON artifacts between isolated role calls. One shared vLLM endpoint serves the pinned Nemotron checkpoint for every role and, when implemented, the single-agent topology. Parallelism is limited to independent reviews or serving-level batching; do not claim parallel role execution for the current workflow.

Mahek owns the agent-side integration points: review request to controller, controller to role prompts, OpenAI-compatible client to vLLM, structured model response to schema validation, runner evidence back to the revision loop, and agent telemetry to `RunLog`. Harsha owns plan execution and verdicts; Avritti owns persisted evidence queries and UI. Cross-owner interface changes require a handoff note and owner review.

## Commands

Prerequisites: Linux with Docker (Docker Desktop is started automatically through `systemctl --user start docker-desktop` if it is down), `git`, and `uv` (it provides Python 3.12). `libmagic` is needed by Saleor. Ports used: 55432 (Postgres), 56379 (Valkey), 8001 (old app), 8002 (new app).

```bash
uv sync                                         # runner venv in ./.venv (runner deps only)
uv run migproto setup                           # services, pinned Saleor clone, worktrees from patches/, Saleor venv in vendor/venv
uv run migproto baseline [--rebuild-seed] [--skip-pytest]   # first run about 7 min (seed 142 s, first pytest DBs about 250 s), later runs about 50 s
uv run migproto unsafe                          # about 22 s
uv run migproto staged --plan proposals/scripted/plan_v2.json   # about 31 s, any plan file works
uv run migproto agent-loop --proposals scripted # no model needed, about 55 s
uv run migproto agent-loop --proposals llm      # live model, see configuration
uv run migproto all [--skip-pytest]             # setup + baseline + unsafe + scripted loop
uv run pytest                                   # 339 tests (85 runner, 109 agents, 81 memory/UI, 64 gym), under 10 s, needs local Postgres for trigger tests
uv run pytest -q tests/memory                   # 50 memory/UI tests, no Docker or Postgres needed
uv run migproto ui [--port 8765] [--db PATH]    # Replay UI at http://127.0.0.1:8765/ (reads examples/ and runs/)
```

Live model configuration through environment variables (see `.env.example`; the code reads the process environment and does **not** auto-load `.env`):

- `MIGPROTO_LLM_BASE_URL` (default `http://localhost:8000/v1`)
- `MIGPROTO_LLM_MODEL` (required for live runs; no development-model fallback)
- `MIGPROTO_LLM_API_KEY` (default `unused`)

Do **not** export `UV_PROJECT_ENVIRONMENT` in your shell. `setup` sets it only for the Saleor sync. If it leaks into `uv run migproto`, uv syncs the runner's lockfile into Saleor's venv.

Exit codes: 0 on `PASS` or `REPRODUCED`, 1 on `FAIL`, 2 on `ERROR`. Every command writes `runs/<YYYYMMDD-HHMMSS-command>/`. That directory is git-ignored. Promote a run to a fixture with `scripts/export_example.py`.

## Evidence rules

1. Only the runner creates executed test observations (`kind: check`). Agents propose, the runner disposes.
2. Agent hypotheses and recommendations are claims. They must cite observation IDs separately and never carry their own pass/fail (`docs/contracts.md`, section 5).
3. Every result identifies its run, attempt, step, application version, and database/schema state where available. Use the v0 derivation rules in `docs/contracts.md` until v1 fields exist.
4. Missing scope means unknown. Never fill it in by assumption.
5. A pass applies only to the conditions tested (data set, versions, sequential interleaving, this machine).
6. Preserve provenance: `manual`, `scripted`, `runner`, `agent:<model>`. A `manual` check definition executed by the runner is still a runner observation.
7. Distinguish failed checks from refusals and execution errors. In v0 all three are `FAIL`. Read `name` and `error` (see contracts G2).
8. When showing a successful final attempt, show the failed attempts too. `summary.json` already keeps all attempts. Never drop them.

## Development rules

- Preserve the working CLI and the reproduction path (`setup` → `baseline` → `unsafe` → `agent-loop`). Run `uv run pytest` and at least `uv run migproto agent-loop --proposals scripted` before merging runner or loop changes.
- Extend `staged.py`, `checks.py` and `migproto/agents/loop.py`. Do not create competing runners or loops.
- Inside `vendor/saleor` and its worktrees, follow Saleor's `AGENTS.md`. Change Saleor code only through `patches/` (regenerate with `git format-patch pinned..<branch>`). Never commit `vendor/`.
- No production database access. SQL runs only on `127.0.0.1` databases named `migproto_run_*`.
- The SQL guard (`db.guard_target`, `db.guard_sql`) is a denylist on disposable local databases. Do not describe it as a security sandbox.
- Do not expose scripted solutions (`proposals/scripted/plan_v2.json`), reference patches (`patches/`), answer labels (`proposals/manual/`), held-out scenarios, or `examples/` outcomes to evaluated agents through prompts, tools or file access.
- Mandatory verification (`checks.MANDATORY_AFTER_DEPLOY`, preservation, column agreement, retirement gate, schema validation) stays in runner code, outside agent control. Agents may add tests, never remove or weaken them.
- Record model identity, endpoint, parameters and budgets for every model run.
- Do not claim zero downtime, concurrency safety, or general migration support beyond what was tested.
- `PG_PASSWORD = "saleor"` and `admin@example.com` / `admin` are Saleor's local development defaults for disposable containers, not credentials. Never commit real keys. `.env` is git-ignored.

## Bounties, models and compute

- Bounties targeted: NVIDIA Nemotron 3.5 Lightning, Red Hat vLLM (Mahek), and NVIDIA NeMo Gym (Harsha). Avritti presents the evidence. See `docs/bounties.md`. Track-specific requirements are **not verified**, so never claim qualification. Keep unimplemented integrations marked planned.
- Parameter budget: the submitted system stays within **33B total parameters** (event rule, verified). Count inactive MoE experts and every draft, embedding, critic or judge model. Keep `docs/model-inventory.md` current with exact checkpoints and counts, and check the remaining budget before adding any model.
- One shared Nemotron 3.5 Lightning vLLM deployment serves all agent roles. Draft models (DSpark, DFlash) stay excluded until organizers confirm accounting.
- `gpt-oss:120b-cloud` is development-only and stays outside the submitted workflow.
- Local development and evaluation with smaller models are allowed. Record the exact model (checkpoint or tag, quantization), endpoint, serving configuration and sampling parameters for every run.
- Compute: the final Nemotron 3.5 Lightning vLLM deployment, and every measurement reported for it, runs only on GPU instances paid for with the **event-provided NVIDIA Brev credits**. Never use a teammate's personal cloud account or personal credits for it. Scripted mode needs no GPU.
- Local development models stay outside the submitted workflow. Report their results separately from the Brev Lightning/vLLM results and never mix them in the same table or claim.
- Priority: (1) small-model loop on vLLM, (2) integrated evidence and UI, (3) NeMo Gym adapter and evaluation, (4) optional serving experiments.

### Brev Lightning quick start

Everything needed to serve Lightning and run reviews is in `scripts/brev/`; the full procedure and the recorded deployment are in `docs/serving.md`. vLLM runs on the Brev GPU and listens on `127.0.0.1` only; the runner, Saleor and Postgres stay on the Linux runner machine and reach vLLM through an SSH tunnel.

1. Create a 1x H100 instance in the event's Brev organization (credits only; the Scaleway H100 at $3.96/h has **no stop/start**, so delete it when done). `brev login`, then check `ssh <name> nvidia-smi`.
2. On the runner machine (Postgres seeded, `uv run migproto baseline --skip-pytest` after any reboot):
   ```bash
   MIGPROTO_LLM_THINKING_BUDGET=4096 CUDA_COMPAT=13-0 \
     uv run python scripts/brev/supervise.py --instance <name> --instance-type "<type>" \
       --started-at <creation time, ISO> --runs 1 --max-tokens 120000
   ```
   It keeps the machine awake, starts `serve_lightning.sh` on the instance (about 3 min from a fresh instance: install, 18 GB download, JIT kernels), holds the tunnel, runs `check_endpoint.sh`, alternates `roles` and `single` reviews, and stops starting runs at `--stop-usd` (default $18, estimated from `--started-at`). Output: `runs/brev-batch-<ts>/` (`supervisor.log`, `runs.jsonl`, serving record). `touch runs/brev-batch-<ts>/STOP` stops after the current run. `--delete-when-done` deletes the instance afterwards. `scripts/brev/summarize.py <batch dir>` prints `docs/results.md` rows.
3. Run it inside tmux (`tmux new -s brev '<command>'`, detach with `Ctrl-b d`, reattach with `tmux attach -t brev`) and watch `supervisor.log`. Add `--workers 4` to run four reviews at once (each worker gets its own app ports 8001+10w, lock and Redis DB); a second supervisor beside a running one needs `--first-worker` past the other's workers and its own `--port`.

Known problems already handled in `serve_lightning.sh` (do not rediscover them):
- **Driver too old:** the Scaleway image has driver 570 (CUDA 12.8); the PyPI vLLM 0.27.1 wheel uses CUDA 13 PyTorch. `CUDA_COMPAT=13-0` installs `cuda-compat-13-0`.
- **JIT toolchain:** Humming (the Hopper NVFP4 fallback) and FlashInfer compile kernels at startup with the pip CUDA toolkit. The script pins `nvidia-cuda-nvcc`, `-crt` and `nvidia-nvvm` to 13.0.88 (the resolver installs 13.4, which FlashInfer rejects against 13.0 headers), puts the venv and `nvidia/cu13/lib` on `PATH` / `LD_LIBRARY_PATH`, sets `CUDA_HOME`, and adds the `lib64`, `libcudart.so` and `libcuda` stub links.
- **SSH:** Brev's `ssh_config` uses `ControlMaster auto`, so the tunnel opens its own connection.
- The model is not gated on Hugging Face; no `HF_TOKEN` is needed. All recipe flags from `docs/bounties.md` are accepted by vLLM 0.27.1.

Findings from the 2026-10-17 smoke tests (runs `20261017-1815*` to `-1832*`, none passed; not results):
- **Uncapped reasoning does not fit a review budget:** single calls generated 18-24k completion tokens (about 250 tokens/s, 75-95 s each); both runs hit a 40k-token budget after 2 and 4 calls. Use `MIGPROTO_LLM_THINKING_BUDGET` (vLLM `thinking_token_budget`; 4096 gave about 2.6k completion tokens per call).
- **Reasoning off:** both runs ended in ERROR before reaching the runner (invalid plans).
- **Capped at 4096 with 120k tokens:** both runs reached the runner; the runner refused the plans (retirement gate; 19-22 failed cross-version checks, no bidirectional sync).
- **Fixed:** the plan response schema let deploy/retire stages carry `"sql": []`, which `plans.validate_plan` rejects; the schema now has separate stage shapes (thinking budget `86e9b34`, schema `7d77778`).
- **Open:** an invalid revision turns a run with a real attempt-1 runner FAIL into `ERROR`; consider `FAIL`/`INCOMPLETE` (controller change). The measurement configuration and the budget-fairness rule are not decided yet.

### Tracing (optional)

`migproto/agents/tracing.py` can send each live review to Langfuse Cloud as a viewer for prompts and replies. It is off unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set in the process environment (`.env` is not auto-loaded; see `.env.example`). `LANGFUSE_BASE_URL` selects the region. Set `LANGFUSE_TRACING_ENVIRONMENT` (for example `dev` for tests, something else for measurement batches) so test traces can be filtered out.

- **What is sent:** one trace per review (`session_id` = run ID, name `review <topology>`). It holds one generation per model request, named after the role (input messages as sent, reply, reasoning, token usage, latency; `ERROR` level for invalid replies, `WARNING` when a valid reply exhausted the budget) and one span per runner attempt (the runner's verdict and failed check names). Trace metadata records topology, model, thinking budget, reasoning flag, `max_revisions` and `request_id`. Tags are `source:`, `topology:` and `verdict:<PASS|FAIL|INCOMPLETE|ERROR|EXCEPTION>`. Prompts, replies and reasoning leave the machine, so do not enable tracing for content that must stay local.
- **Run link:** `runs/<run_id>/agents/trace.json` stores the trace ID and URL. `summary.json` is unchanged.
- **Evidence:** traces are a viewer. They never decide anything and are not evidence. `events.jsonl` and `summary.json` from `RunLog` remain the record, and only the runner produces checks. A tracing failure (bad key, network) is logged and never fails a review.
- **Tests:** `tests/agents/conftest.py` clears `LANGFUSE_*` so the suite never sends traces; tracing tests use the real SDK with an in-memory exporter.

## Collaboration

| Role | Assignment |
|---|---|
| Owner 1: Harsha, runner and verification | `docs/team/01-runner.md` |
| Owner 2: Mahek, agents and orchestration | `docs/team/02-agents.md` |
| Owner 3: Avritti, scoped memory and UI | `docs/team/03-memory-ui.md` |

Overview and integration order: `docs/team/README.md`. Contracts: `docs/contracts.md` (changes coordinated by Harsha).

If the user states their role, work only on that assignment and its necessary dependencies. If no role is given, read `docs/team/` and ask which role to take before modifying implementation. Do not silently take over another teammate's modules. Propose cross-module or contract changes in your handoff notes instead.

For Mahek's work (Owner 2), changes are limited to the owned agent paths and documentation listed in `docs/team/02-agents.md`. Shared CLI changes require Harsha's review. Do not modify runner verification, memory/UI, NeMo Gym, Saleor worktrees, or shared contracts as part of agent implementation; document the requested interface change for the appropriate owner instead.
