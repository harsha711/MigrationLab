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
- **Status of the 120B run:** it is a development smoke test. It is **not** our qualifying small-model implementation, and it sits outside the 33B parameter budget. No Nemotron Lightning, vLLM or NeMo Gym integration exists yet.
- **Multi-agent claim:** no superiority over a single agent has been established. No single-agent baseline exists yet.
- **Runner tests:** `uv run pytest` runs 34 tests covering the SQL guard, plan schema, and trigger semantics on a scratch table.

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
| `migproto/agents/llm.py` | OpenAI-compatible chat client (plain `requests`, JSON replies) | Owner 2 |
| `migproto/agents/roles.py` | investigator, planner, tester prompts. `gather_hits` search tool, investigator scoring | Owner 2 |
| `migproto/agents/loop.py` | controller: investigate, plan, propose tests, execute, revise at most once | Owner 2 |
| `proposals/scripted/` | scripted proposals that run the loop without a model | Owner 2 |
| `proposals/manual/`, `proposals/negative/`, `patches/` | answer labels, negative fixtures, reference app patches | Harsha |
| `tests/` | runner tests | Harsha (new agent and memory tests go in `tests/agents/`, `tests/memory/`) |
| `scripts/export_example.py`, `examples/` | sanitized fixture export and frozen fixtures | Harsha |
| `docs/contracts.md` | shared data contracts and gap register | Harsha (coordinator) |
| `migproto/memory/`, `migproto/ui/` (planned, not created) | evidence adapter, SQLite store, review UI | Owner 3 |
| `gym/` (planned, not created) | NeMo Gym resources server wrapping the runner, frozen tasks, results | Harsha |
| `docs/serving.md`, `docs/results.md` (planned), `docs/model-inventory.md` | vLLM serving record, measured results, model and parameter inventory | Owner 2 |
| `docs/bounties.md` | bounty requirements (verified versus unverified), owners, status, open questions | Harsha (coordinator) |

Data flow: `migproto/agents/loop.py` asks roles for proposals. It passes plan and tests to `staged.run`, which starts `old` and `new-staged` servers on a fresh `migproto_run_*` clone of the `migproto_seed` template. The runner executes SQL stages through `db.run_guarded` and records every observation through `RunLog`.

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
uv run pytest                                   # 34 runner tests, under 2 s, needs local Postgres for trigger tests
```

Live model configuration through environment variables (see `.env.example`; the code reads the process environment and does **not** auto-load `.env`):

- `MIGPROTO_LLM_BASE_URL` (default `http://localhost:11434/v1`)
- `MIGPROTO_LLM_MODEL` (default `gpt-oss:120b-cloud`)
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

- Bounties targeted: NVIDIA Nemotron 3.5 Lightning, Red Hat vLLM (Owner 2), and NVIDIA NeMo Gym (Owner 1). Owner 3 presents the evidence. See `docs/bounties.md`. Track-specific requirements are **not verified**, so never claim qualification. Keep unimplemented integrations marked planned.
- Parameter budget: the submitted system stays within **33B total parameters** (event rule, verified). Count inactive MoE experts and every draft, embedding, critic or judge model. Keep `docs/model-inventory.md` current with exact checkpoints and counts, and check the remaining budget before adding any model.
- One shared Nemotron 3.5 Lightning vLLM deployment serves all agent roles. Draft models (DSpark, DFlash) stay excluded until organizers confirm accounting.
- `gpt-oss:120b-cloud` is development-only and stays outside the submitted workflow.
- Compute: serve models and make live model calls only on GPU instances paid for with the **event-provided NVIDIA Brev credits**. Never use a teammate's personal machine, personal cloud account or personal credits. Scripted mode needs no GPU.
- Priority: (1) small-model loop on vLLM, (2) integrated evidence and UI, (3) NeMo Gym adapter and evaluation, (4) optional serving experiments.

## Collaboration

| Role | Assignment |
|---|---|
| Owner 1: Harsha, runner and verification | `docs/team/01-runner.md` |
| Owner 2: agents and orchestration | `docs/team/02-agents.md` |
| Owner 3: scoped memory and UI | `docs/team/03-memory-ui.md` |

Overview and integration order: `docs/team/README.md`. Contracts: `docs/contracts.md` (changes coordinated by Harsha).

If the user states their role, work only on that assignment and its necessary dependencies. If no role is given, read `docs/team/` and ask which role to take before modifying implementation. Do not silently take over another teammate's modules. Propose cross-module or contract changes in your handoff notes instead.
