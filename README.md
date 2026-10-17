# migproto: migration reviewer prototype

We are building a migration reviewer that **generates and revises staged database migration plans from execution evidence**. It runs each plan against an old and a new application version serving the same database at the same time, and records what each version observed at each schema state.

Target application: [Saleor](https://github.com/saleor/saleor) pinned at `0c0e2697cc72aa999f7786ae8aeda7665bd4285f`. Saleor already has migration compatibility testing (`.github/workflows/test-migrations-compatibility.yml` applies a PR's migrations and then runs the base branch's tests). Our intended contribution is different: generating and revising staged plans using evidence gathered while old and new versions coexist.

## What currently works

Feasibility checkpoint `feasibility-v1`. Details and measurements are in [REPORT.md](REPORT.md).

| Evidence type | What it is | Result |
|---|---|---|
| Existing upstream tests | 4 Saleor account test files run on the `old` and `new-unsafe` code | 110 of 110 passed on each |
| Custom staged-plan verification | our runner: per-stage checks, 8 mandatory interleaved old/new cases, preservation, column agreement, retirement gate | see the rows below |
| Scripted demonstration: unsafe rename | intentionally introduced `User.note` → `customer_note` immediate rename | migration exits 0, new app works, old app read, write and create fail (`examples/unsafe-rename`) |
| Scripted demonstration: one-way sync (plan v1, manually authored) | the new app's writes are mirrored, the old app's are not | 9 of 29 checks failed, contract refused (`examples/scripted-sync-failure`) |
| Scripted demonstration: staged alternative (plan v2, manually authored) | bidirectional trigger, batched backfill, retire, contract | 33 of 33 checks passed (`examples/scripted-staged-success`) |
| Live model result | one `gpt-oss:120b-cloud` run (development smoke test, not our small-model target) | attempt 1 refused by `retirement_gate` (contract before retire), attempt 2 reordered and passed 41 of 41. 30,036 tokens, 88.2 s (`examples/live-ordering-revision`) |

Scripted demonstrations show that the runner detects the intended failures. They are not agent results. The live result is a single run and establishes no comparison between single-agent and multi-agent setups.

## Reproduce

Prerequisites: Linux, Docker, `git`, [`uv`](https://docs.astral.sh/uv/) (it supplies Python 3.12), `libmagic`. Free ports 55432, 56379, 8001 and 8002.

```bash
uv sync                                         # runner environment in .venv
uv run migproto setup                           # Postgres 15 + Valkey, Saleor clone + worktrees in vendor/
uv run migproto baseline                        # seed template DB, old-app smoke, upstream test subset (first run about 7 min)
uv run migproto unsafe                          # reproduce the old-app breakage
uv run migproto staged --plan proposals/scripted/plan_v1.json   # expect FAIL
uv run migproto staged --plan proposals/scripted/plan_v2.json   # expect PASS
uv run migproto agent-loop --proposals scripted # full loop without a model
uv run pytest                                   # 34 runner tests
```

`uv run migproto all` runs setup, baseline, unsafe and the scripted loop in one go. Each command writes `runs/<timestamp>-<command>/` (git-ignored) with `events.jsonl`, `summary.json`, SQL, server logs, and agent artifacts.

### Running without a model

`--proposals scripted` uses the manually authored files in `proposals/scripted/`: an investigator output, the flawed plan v1, the revised plan v2, and tester cases. It exercises the full loop and runner (attempt 1 fails, attempt 2 passes) without any endpoint.

### Configuring a live endpoint

Any OpenAI-compatible `/chat/completions` endpoint works. Set these in your shell (see [.env.example](.env.example); `.env` is not loaded automatically):

```bash
export MIGPROTO_LLM_BASE_URL=http://localhost:11434/v1   # default (Ollama)
export MIGPROTO_LLM_MODEL=gpt-oss:120b-cloud             # default; cloud-backed, sends prompts off-machine
export MIGPROTO_LLM_API_KEY=unused                        # set a real key only in your shell or .env
uv run migproto agent-loop --proposals llm
```

## Team

| Owner | Assignment |
|---|---|
| 1, Harsha: runner and verification, contract coordination | [docs/team/01-runner.md](docs/team/01-runner.md) |
| 2: agents and orchestration | [docs/team/02-agents.md](docs/team/02-agents.md) |
| 3: scoped memory and review UI | [docs/team/03-memory-ui.md](docs/team/03-memory-ui.md) |

Start with [docs/team/README.md](docs/team/README.md), [AGENTS.md](AGENTS.md) (rules for humans and coding agents) and [docs/contracts.md](docs/contracts.md) (data shapes, IDs, gap register). Fixtures are in [examples/](examples/README.md).

### Integration checklist

1. Freeze example events and contracts.
2. Runner → evidence adapter.
3. Agents → runner and scoped retrieval.
4. UI → recorded runs.
5. UI → live review.
6. Small-model single-agent versus multi-agent comparison.

## Bounties and model budget

We target the NVIDIA Nemotron 3.5 Lightning, Red Hat vLLM and NVIDIA NeMo Gym bounties at the NVIDIA x Red Hat Small Models Hack. All three integrations are **planned, not implemented**, and track-specific requirements are still unverified. See [docs/bounties.md](docs/bounties.md) for requirements, owners, status and open questions, and [docs/model-inventory.md](docs/model-inventory.md) for the ≤ 33B total-parameter budget. The submitted system will use one shared Lightning deployment on vLLM, served with event-provided NVIDIA Brev credits. The earlier `gpt-oss:120b-cloud` runs are development-only.

## Current limitations

- One field, one rename pattern, one target commit. The scenario is intentionally introduced.
- Interleaving is sequential request by request. No measured concurrency, locking or load behaviour. Nothing here shows zero downtime.
- The SQL guard is a denylist for disposable local `migproto_run_*` databases, not a security sandbox.
- Events are v0: run, attempt, stage, app version and schema state are not explicit on each observation, and `FAIL` mixes assertion failures, refusals and execution errors (see `docs/contracts.md`).
- Start-state equivalence across machines or seed rebuilds is not recorded.
- Only the roles topology exists. There is no single-agent baseline, no token budget enforcement, and no small-model run yet.
- The live trigger design passed the catalogue but has not been reviewed beyond it.
- Celery, webhooks, read replicas and other old-app `User` paths were not exercised individually.

## Repository layout

```
migproto/            runner, checks, CLI (migproto/agents/: LLM client, roles, loop)
proposals/           scripted/ (loop without a model), manual/ (answer labels), negative/ (gate fixture)
patches/             manual Saleor patches for new-unsafe and new-staged (applied by `setup`)
tests/               runner tests
examples/            sanitized fixtures from real runs
scripts/             export_example.py (run -> fixture)
docs/                contracts.md, team/
vendor/, runs/       local only (git-ignored): Saleor checkout + venv, raw run logs
```
