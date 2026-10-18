# Assignment: NeMo Gym integration (handoff, 2026-10-17 night)

You own `gym/` and `tests/gym/` from now on. Harsha stays on the Lightning measurements and
merges into `brev/lightning-serving`. Deadline: **11:00 on 2026-10-18**.

## Where to work

| | |
|---|---|
| Worktree | `/home/harsha/Documents/hackathon-gym` |
| Branch | `feat/nemo-gym` (PR #5 into `brev/lightning-serving`) |
| Read first | `AGENTS.md` (evidence and development rules), `gym/README.md` (design, commands, what is verified), `gym/HANDOFF.md` (open interface requests) |
| Do not commit | `vendor` (a symlink to the main checkout's Saleor builds) and `migproto.lock` |

Run `git status` and `git log --oneline -5` first. The previous agent's last commit is
`01b2e70` (`eval_policy.py`). If you see uncommitted changes you did not make, ask Harsha
before touching them.

## What exists (all on `feat/nemo-gym`)

- **14 frozen single-step tasks**, one per scenario, in `gym/tasks/migration_review.jsonl`.
  Prompts are built only from agent-side facts.
- **Verify (`gym/migration_review/scoring.py`):** the answer is turned into a plan, checked by
  `facts.plan_validator`, executed by the real runner (`runner_api.execute_plan`), and given a
  binary reward against the manual labels in `proposals/manual/scenarios/`. Runner crashes
  return `mask_sample: true`.
- **NeMo Gym 0.7.0 resources server** (`gym/resources_server/migproto_review/`). It calls
  `gym/verify_cli.py` through `uv run`, because nemo-gym needs Python 3.13.14 or newer and
  migproto pins 3.12.
- **`gym/eval_policy.py`** scores an OpenAI-compatible endpoint on the frozen tasks and writes
  `gym/results/<timestamp>/`. It is tested only against a fake endpoint.
- **Verified so far:**
  - 298 tests pass;
  - scripted-plan smoke runs pass (add-table: reward 1.0; drop-column unsafe plan: reward 0.0);
  - server tests pass on Python 3.13.11, below the declared 3.13.14 (see the README).
- **Not verified:** any model, any NeMo Gym agent or rollout (`gym env start`, `gym eval run`),
  any trainer.

## Tasks, in priority order

### 1. Refresh the frozen tasks after Harsha's push (blocking; about 15 min)

Harsha will push two prompt changes to `origin/brev/lightning-serving`:
- **The deploy-variant guard** (`30a6304`): adds one line to every non-rename scenario prompt.
- **Configuration D** (`ebe6f40`): the rename contract lists the `state_only` and `apply`
  builds.

Both make `tests/gym/test_task_isolation.py::test_frozen_tasks_are_current` fail until the
tasks are rebuilt. This was already checked: after a rebuild, all 294 tests pass.

```bash
git fetch origin && git merge origin/brev/lightning-serving
uv run python gym/build_tasks.py && uv run python gym/build_tasks.py --check
uv run pytest -q
git commit -am "gym: rebuild frozen tasks for the variant guard and rename build lines"
```

Ask Harsha whether the push has happened. Until it has, skip to task 3.

### 2. First real measurement: Lightning on the 14 tasks (Harsha's go-ahead needed)

The GPU is a Brev H100 paid for with event credits. **Never create, stop or delete Brev
instances, and never use personal credits.** Harsha's supervisors hold SSH tunnels to the
running vLLM server on this machine (`127.0.0.1:8200`, `8300`, `8400`, each open while its
batch runs). Ask Harsha which port to use, then:

```bash
MIGPROTO_LLM_BASE_URL=http://127.0.0.1:<port>/v1 MIGPROTO_LLM_MODEL=nemotron-3.5-lightning \
MIGPROTO_LLM_THINKING_BUDGET=16384 uv run python gym/eval_policy.py
```

- **Thinking budget:** use 16384, the same as the agent-loop configurations, so the reasoning
  cap is not a difference. Record it in `config.json` (the script does this).
- **Duration:** 14 tasks, one at a time, about 1-2 min each (model call plus about 50 s of
  runner).
- **Before starting:** first run `--dry-run --tasks add-table` to check the plumbing.
- **Keep every result,** including zero rewards, invalid plans and masked samples. Never rerun a
  task to replace a bad answer. If you run it again, keep both result directories and report
  both.

### 3. Write up the Gym result (about 30 min, after task 2)

Add a **separate** "NeMo Gym evaluation" section to `gym/README.md`, and tell Harsha so it can
go into `docs/results.md`:
- **Per task:** reward, status, runner verdict, the agent's and the runner's recommendation,
  failed checks, tokens and latency.
- **Configuration:** model, endpoint, sampling, thinking budget, task file sha256 and commit
  (all in `config.json`).
- **Not comparable with `agent-loop` reviews:** single-step, no investigator model, no tester
  cases, no revision. Never put the two in one table.
- **Different reward rule:** Gym's reward also requires the policy's recommendation to equal the
  runner's. `scripts/brev/score_scenarios.py` does not require this.

### 4. Run it through NeMo Gym itself, if time allows (about 1-2 h)

This would replace "implemented, not run against NeMo Gym" with something measured.
- **Setup:** install the server into a NeMo Gym 0.7.0 checkout as described under "Installing
  into a NeMo Gym checkout" in `gym/README.md`, on Python 3.13.14 or newer if you can get it.
- **Run:** `gym env test`, then `gym eval run` on a subset of 2-3 tasks, with the policy model
  served by the same Lightning endpoint (a `responses_api_models` server pointing at the vLLM
  URL; check NeMo Gym's docs for the OpenAI-compatible model server).
- **Record:** the exact commands, versions and outputs. Whatever does not work, write down as
  "not verified" with the error.

### 5. Small open items from `gym/HANDOFF.md` (only if 1-3 are done)

- **`.gitignore`:** add `migproto.lock`.
- **Run records:** where `gym-verify` run directories should go at scale. Propose it; don't
  change the runner.
- **Shared `target()`:** leave it. Harsha moves `target()` into one shared module after the
  night's batches.

## Rules

- **Machine:** this laptop also runs Harsha's live batches. Only start Saleor through the Gym
  settings (`gym/migration_review/env.py`: ports 8081/8082, Redis DB 8, this worktree's lock).
  Ports 8001-8072, 8091-8092 and 8101-8142, and Redis DBs 0-7 and 9-14, belong to other work.
  If free memory (`free -m`, "available") drops below about 1.5 GB, pause and tell Harsha.
- **Off-limits files:**
  - Do not edit `migproto/agents/*`, `migproto/staged.py`, `checks.py`, `plans.py`, `db.py`,
    `config.py`, `scripts/brev/*` or anything under `vendor/`.
  - Write interface requests into `gym/HANDOFF.md`.
- **Prompt isolation:** never put content from `proposals/manual/`, `proposals/scripted/`,
  `patches/` or `examples/` into a task or prompt. `tests/gym/test_task_isolation.py` must keep
  passing.
- **Secrets:** never read or print `.env`.
- **Claims:**
  - Only the runner produces pass/fail.
  - A result covers only the conditions tested: synthetic seed, sequential interleaving, this
    machine, one answer per task.
  - Never claim bounty qualification. Whether the NeMo Gym bounty needs training or accepts an
    environment plus evaluation is **unknown** (`docs/bounties.md`).
- **Commits:** small commits on `feat/nemo-gym`. Push them to update PR #5, and tell Harsha
  before each push. Do not push to `brev/lightning-serving` or `main`.

## Who to ask

| Topic | Person |
|---|---|
| GPU endpoint, pushes, merges, results wording | Harsha |
| Runner behaviour (`runner_api`, checks) | Mahek |
| UI/memory ingestion of `gym-verify` runs | Owner 3 |
