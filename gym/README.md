# NeMo Gym integration: migration review

Status: **implemented, not run against a NeMo Gym trainer.** No rollout collection (`gym eval run`), no `gym env start`, no RL or SFT step has been run with it. Nothing here claims bounty qualification (see `docs/bounties.md`).

This directory wraps the deterministic MigrationLab runner as a NeMo Gym resources server, so a model policy can be evaluated (and later trained) on our migration-review tasks. The runner stays the only source of pass/fail.

## NeMo Gym version and sources followed

| Source | What was used | Status |
|---|---|---|
| PyPI `nemo-gym` **0.7.0** (latest release on 2026-10-17; `requires_python >=3.13.14`) | sdist source read for the interface: `nemo_gym/base_resources_server.py` (`SimpleResourcesServer`, `BaseResourcesServerConfig`, `BaseVerifyRequest`, `BaseVerifyResponse` with `reward`, `mask_sample`, `failure_kind`, `failure_reason`; `ReverifyMode`), `nemo_gym/failure_kinds.py` (`verifier_error`), and the layouts of `resources_servers/bird_sql` and `resources_servers/example_single_tool_call` (`app.py`, `configs/*.yaml`, `data/*.jsonl`, `requirements.txt`, `tests/`) | verified against the 0.7.0 source |
| [docs.nvidia.com/nemo/gym/latest/environment-tutorials/single-step-environment.html](https://docs.nvidia.com/nemo/gym/latest/environment-tutorials/single-step-environment.html), fetched 2026-10-17 (page says "version Main") | resources server routes (`/seed_session`, tools, `/verify`), task JSONL with `responses_create_params`, config YAML keys (`entrypoint`, `domain`, `verified`, `description`), `gym env ...` / `gym eval run` commands | read; the 0.7.0-versioned copy of this page does not exist, so the page may describe unreleased `main` |
| Task JSONL: extra top-level fields of a task line reach `verify` | seen in `bird_sql` (its `gt_sql`, `db_id` fields are declared on its verify request model) | verified in 0.7.0 source, **not verified** by running a NeMo Gym agent |
| Whether `responses_create_params` accepts a `text.format` JSON schema (structured output) | not used | not verified |
| `gym env test`, `gym env start`, `gym eval run` with this server | not run | not verified |

The server tests ran with nemo-gym 0.7.0 installed in a separate venv on **Python 3.13.11**, below the package's declared `>=3.13.14` (installed with `uv pip install --python-version 3.13.14`, because the local uv could not provide 3.13.14). Re-run them on 3.13.14+ before relying on them.

## Layout

```text
gym/
  tasks/migration_review.jsonl     frozen tasks, one per scenario in migproto/scenarios.py (14)
  migration_review/                framework-independent core (runner venv, Python 3.12)
    tasks.py                       prompt and task builder (agent-side facts only)
    scoring.py                     extract plan -> facts.plan_validator -> runner -> reward
    env.py                         runner settings for Gym (ports 8081/8082, Redis DB 8, lock)
  build_tasks.py                   freeze / --check the task file
  verify_cli.py                    stdin JSON -> scoring.verify -> stdout JSON (the bridge)
  smoke.py                         verify one scenario with a scripted plan, no model
  resources_server/migproto_review/
    app.py                         NeMo Gym resources server (nemo-gym 0.7.0)
    configs/migproto_review.yaml   server + simple_agent + environment server config
    requirements.txt               `-e nemo-gym[dev] @ ../../` (as in NeMo Gym's own servers)
    tests/test_app.py              server tests (run in a NeMo Gym venv)
  HANDOFF.md                       interface requests for other owners
tests/gym/                         MigrationLab-side tests (no Docker; staged.run mocked)
```

Why two environments: nemo-gym 0.7.0 needs Python ≥ 3.13.14, `migproto` pins Python 3.12. The server's `/verify` therefore runs `uv run --project <checkout> python gym/verify_cli.py` as a subprocess and parses its last stdout line. No runner code is duplicated; `verify_cli.py` calls `migproto.runner_api.execute_plan`, which runs `migproto.staged.run` under the repository execution lock (the same entry point the agent controller uses).

## Task

Single-step: the policy gets a system and user message and answers with one migration plan as JSON. No tools are exposed, there is no tester role and no revision.

Each line of `tasks/migration_review.jsonl`:

```json
{"task_id": "migproto-add-table", "scenario_id": "add-table", "task_version": 1,
 "prompt_sha256": "...", "responses_create_params": {"input": [
   {"role": "system", "content": "<roles.PLANNER_SYSTEM>"},
   {"role": "user", "content": "<scenario text, contract, mandatory cases, search hits>"}]}}
```

The user message is assembled from the same agent-side facts the controller gives its planner: `request.scenario_text(request.scenario_request(id))`, `facts.for_scenario(id).contract` (rename-note: `roles.PLAN_CONTRACT` plus a recommendation paragraph, because that contract predates the field), the mandatory case catalogue (`facts.render_cases`), and the runner's deterministic search of the scenario's OLD app version (`roles.gather_hits`, experiment annotations removed). The controller's investigator step is replaced by these raw hits. The task never contains anything from `proposals/manual/`, `proposals/scripted/`, `patches/`, `examples/` outcomes, expected verdicts, or the scenario title and tags. `tests/gym/test_task_isolation.py` checks this, including that the builder opens no file under those directories.

Rebuild after changing scenarios, facts or prompts (needs the `vendor/wt-*` worktrees):

```bash
uv run python gym/build_tasks.py           # rewrite
uv run python gym/build_tasks.py --check   # exit 1 if stale (also a test when worktrees exist)
```

## Verify and reward

`scoring.verify(scenario_id, output_text, model)`:

1. Extract the plan object from the answer (whole answer, else last fenced block, else last top-level object with `stages`; text before `</think>` ignored). None: `no_plan_json`, reward 0, runner not run.
2. `facts.plan_validator(facts.for_scenario(id))`. `PlanError`: `invalid_plan`, reward 0, runner not run.
3. Run the plan: `runner_api.execute_plan(facts.runner_plan(plan) minus "provenance", scenario=id)`, mandatory runner checks only (no tester cases), provenance `agent:<response.model>`. One `runs/<ts>-gym-verify/` directory per call, with `summary.json` holding runner facts and the policy's recommendation claim but **no reward and no expected verdict**.
4. Reward, against the manual labels in `proposals/manual/scenarios/<id>.json` (read only here). Same `target()` rule as `scripts/brev/score_scenarios.py` on `brev/lightning-serving`:
   - a safe plan exists (some labelled plan is a runner PASS): reward 1.0 iff runner verdict `pass`, runner recommendation in the labelled PASS recommendations, **and** the policy's `recommendation` equals the runner's;
   - no safe plan (only `unique-last-name` today): reward 1.0 iff the policy recommends `reject` and the runner failed or refused its schema-valid plan (runner recommendation `reject`). A runner `error` verdict does not count;
   - `rename-note` has no label file; as in that script, any runner PASS is on target.
5. If the runner raises (lock busy, Postgres down, crash): `runner_exception`, reward 0, `mask_sample: true`, `failure_kind: verifier_error`, so it is not scored as a policy failure.

Returned info (also on the NeMo Gym verify response): `status`, `policy_recommendation`, `plan_id`, `run_id`, `attempt_id`, `runner_verdict` (`pass|fail|refused|error`), `runner_recommendation`, `failed_checks` (runner check names), `halted`, `error`.

Known gaming risk for the reject case: a policy that recommends `reject` with any schema-valid plan the runner fails is rewarded on `unique-last-name`. With one such task in 14 this matters little for evaluation; it matters for training. Record rewards per scenario.

## Runner settings

Anything that starts Saleor uses `migration_review/env.py`, which overrides the shell: `MIGPROTO_PORT_OLD=8081`, `MIGPROTO_PORT_NEW=8082`, `MIGPROTO_CACHE_URL=redis://127.0.0.1:56379/8`, `MIGPROTO_LOCK_PATH=<this checkout>/migproto.lock` (live GPU batches use ports 8001-8072 and Redis DBs 0-7). `verify_cli.py --keep-env` keeps the caller's `MIGPROTO_*` instead. One verification at a time per lock (server `max_concurrency: 1`); a call takes about 50 s.

## Commands

```bash
uv run pytest -q tests/gym                  # 64 tests, no Docker (staged.run mocked)
uv run python gym/smoke.py                  # real runner, add-table, scripted accept plan
uv run python gym/smoke.py --scenario drop-column \
    --plan proposals/scripted/drop-column/safe.json --recommendation staged_rollout

# Server tests, in a NeMo Gym 0.7.0 venv (Python >= 3.13.14):
cd gym/resources_server/migproto_review && <venv>/bin/python -m pytest tests -q
```

Installing into a NeMo Gym checkout (planned, not done): copy or symlink `gym/resources_server/migproto_review` to `resources_servers/migproto_review`, copy `gym/tasks/migration_review.jsonl` to `resources_servers/migproto_review/data/`, set `migproto_root` in the config, then follow the NeMo Gym docs (`gym env test --resources-server migproto_review`, `gym env start ...`, `gym eval run ...`). The scripted plan in `smoke.py` stands in for a policy; scripted plans are never shown to a policy.

## Evaluating a policy model (`gym/eval_policy.py`)

```bash
MIGPROTO_LLM_BASE_URL=http://localhost:8000/v1 MIGPROTO_LLM_MODEL=nemotron-3.5-lightning \
MIGPROTO_LLM_THINKING_BUDGET=4096 uv run python gym/eval_policy.py [--tasks add-table,drop-column]
uv run python gym/eval_policy.py --dry-run --tasks add-table    # fake local endpoint, no model
```

One task at a time: it sends the task's `responses_create_params.input` to `<base_url>/chat/completions` with the settings `migproto/agents/llm.py` reads from `MIGPROTO_LLM_*` (read through `LLM` itself: temperature, top_p, `chat_template_kwargs`, `thinking_token_budget`). It then scores the reply with `scoring.verify`. Defaults:
- `--response-format schema` sends the controller planner's guided-decoding schema for the scenario's builds. The alternatives are `json_object` and `none`.
- One answer per task, with no correction turn. Transport failures are retried (`--retries 2`) and then recorded as `model_error` with `mask_sample: true`.

It calls the endpoint directly, not through a NeMo Gym agent or model server.

Output in `gym/results/<timestamp>/`:
- `config.json`: model, endpoint, sampling, thinking budget, serving metadata, nemo-gym version, task file sha256, runner env and git commit. The API key is never written.
- `results.jsonl`: `task_id`, `reward`, `status`, `mask_sample`, `run_id`, `failed_checks`, `tokens`, `latency_s`, recommendations and runner verdict.
- `answers/<task_id>.json`: the raw reply and reasoning.
- `summary.json`.

`--dry-run` answers every task with a fixed direct-deploy plan (`accept_as_is`) from a fake endpoint on 127.0.0.1. Its results go to `<timestamp>-dry-run/`, which is git-ignored. The runner still executes that plan, so each task starts Saleor (ports 8081/8082, Redis DB 8, this checkout's lock, about 50 s). A dry-run reward measures the plumbing, not a model.

## What was verified (2026-10-17, this machine)

- `uv run pytest -q`: 298 passed, including 64 in `tests/gym` (6 for `eval_policy.py` against a fake endpoint).
- `uv run python gym/eval_policy.py --dry-run --tasks add-table`: fake endpoint, real runner, reward 1.0, run `20261017-213741-gym-verify`. `eval_policy.py` has not been run against a real model.
- Server tests (`tests/test_app.py`, 6 passed) with nemo-gym 0.7.0 on Python 3.13.11 (see the version note above): verify forwards the answer and returns runner info, verifier crashes are masked, `POST /verify` and `GET /reverify_mode` through the server's own FastAPI app, and the real `uv run gym/verify_cli.py` bridge on answers that never reach the runner.
- `uv run python gym/smoke.py` (add-table, scripted accept plan): runner PASS, 18 of 18 checks passed, reward 1.0, run `20261017-212153-gym-verify` (52.0 s).
- Full path, server `verify()` → `uv run` bridge → real runner (drop-column, scripted unsafe plan with `accept_as_is`): reward 0.0, runner `fail`/`reject`, failed checks `case.deploy_new.old_smoke`, `case.old_write_old_read`, `case.old_create_old_read`, `case.new_create_plain_old_read` (4 of 12 checks failed), run `20261017-212259-gym-verify` (54.4 s). This agrees with the manual label for that plan (FAIL, reject).

These two runs used scripted plans: they check the plumbing, they are not policy results. A pass applies only to the conditions tested (synthetic seed data, sequential interleaving, this machine).

## Not verified

- Any NeMo Gym agent, environment server, rollout collection or trainer using this server.
- Behaviour on Python ≥ 3.13.14 (the declared nemo-gym requirement).
- Any model policy on these tasks (no model was run; no GPU, no Brev).
- That single-step scores are comparable to `agent-loop` reviews (different workflow: no investigator model, no tester cases, no revision).
