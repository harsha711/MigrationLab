# Agent and Model Validation Guide

This document is the runbook for validating the Owner 2 agent layer, first without a model
and then with Nemotron 3.5 Lightning served by vLLM. It records the expected inputs, outputs,
evaluation data, environment, commands, and the evidence that exists today.

## Current truth

| Item | Status |
|---|---|
| Agent controller unit tests | **Validated:** 14 tests passed on 2026-10-17 |
| Guard, schema, and agent tests together | **Validated:** 40 tests passed on 2026-10-17 |
| GPU-free scripted integration | Existing frozen fixture passes after one planned revision; not rerun in the current Windows workspace |
| `gpt-oss:120b-cloud` | One real development smoke run exists; outside the 33B submission budget |
| Nemotron client/controller integration | Implemented and mock-validated |
| Nemotron checkpoint served through vLLM | **Not run yet** |
| Live `roles` topology on Nemotron | **Not run yet** |
| Live `single` topology on Nemotron | **Not run yet** |
| Topology comparison | **Not measured yet** |

No Nemotron result or bounty qualification should be claimed until the live rows above are
replaced by recorded runs.

## What is the dataset?

There is no model-training dataset in this repository. The system uses an execution-based
evaluation fixture for one intentionally introduced Saleor migration scenario.

### Application and scenario

- Application: Saleor at commit `0c0e2697cc72aa999f7786ae8aeda7665bd4285f`.
- Change: rename Django `User.note` / PostgreSQL `account_user.note` to
  `customer_note`, while keeping the public GraphQL field named `note`.
- Versions: unmodified `old`, immediate-rename `new-unsafe`, and state-only-migration
  `new-staged`.
- The rename is an experiment, not an upstream Saleor defect.

### Database records

The `migproto_seed` template contains:

- Saleor data created by `populatedb --createsuperuser --withoutimages`;
- 200 synthetic customers named `customer0000@seed.migproto.test` through
  `customer0199@seed.migproto.test`;
- 245 users total in the recorded fixture;
- 191 users with non-null notes in the recorded fixture.

Synthetic note generation uses Python `random.Random(42)`. It covers:

- `NULL` and empty values;
- Unicode text;
- quotes and SQL-looking text that must remain ordinary data;
- newlines and tabs;
- a 2,000-character value;
- varied short word sequences.

The 200 synthetic notes are deterministic, but Saleor's surrounding `populatedb` output is
not proven identical across rebuilds or machines. Every plan attempt clones a fresh disposable
`migproto_run_*` database from the local template. Start-state hashes are a pending Owner 1
contract improvement.

### Code context supplied to the model

The Investigator receives a deterministic search for the word `note` under:

```text
saleor/account
saleor/graphql/account
saleor/core/utils/anonymization.py
```

Migration files are excluded. The current scenario produces 35 search hits. Evaluated agents
must not receive scripted plans, manual answer labels, patches, held-out scenarios, or frozen
example outcomes.

### Verification cases

The runner, not the model, owns mandatory verification. It exercises old/new reads, writes,
creates, clears, alternating writes, preservation, column agreement, schema state, and the
retirement gate. Agent-proposed cases can be added but cannot remove mandatory cases.

The checks are sequential request interleavings. They do not establish concurrent-write,
load, lock, or general zero-downtime safety.

## Inputs

### Default CLI input

The backward-compatible invocation needs no request file:

```bash
uv run migproto agent-loop --proposals scripted
uv run migproto agent-loop --proposals llm --topology roles
```

The controller constructs the current rename request automatically.

### Review request input

For an explicit review, pass `--request review.json`:

```json
{
  "contract_version": "migproto.contracts/v1",
  "request_id": "req-user-note-rename-001",
  "target": {
    "app": "saleor",
    "commit": "0c0e2697cc72aa999f7786ae8aeda7665bd4285f"
  },
  "change": {
    "description": "Rename Django field User.note to customer_note while keeping the public GraphQL field note.",
    "table": "account_user",
    "old_column": "note",
    "new_column": "customer_note",
    "intentionally_introduced": true
  },
  "app_versions": {"old": "old", "new": "new-staged"},
  "mode": "llm",
  "agent_topology": "roles",
  "budget": {
    "max_revisions": 1,
    "max_model_calls": 10,
    "max_total_tokens": 40000,
    "max_wall_seconds": 600
  }
}
```

`--topology roles|single` overrides `agent_topology`. Both topologies must use the same total
budget in comparisons.

### Model-serving input

Live mode requires:

```bash
export MIGPROTO_LLM_BASE_URL=http://<brev-host>:8000/v1
export MIGPROTO_LLM_MODEL=nemotron-3.5-lightning
export MIGPROTO_LLM_API_KEY=unused
```

Serving metadata and sampling variables are listed in `.env.example`. The process does not
automatically load `.env`.

## Expected agent outputs

### Investigator

```json
{
  "findings": [
    {
      "ref": "path.py:123",
      "kind": "read",
      "needs_change": true,
      "reason": "ORM attribute uses the renamed model field"
    }
  ],
  "implicit_paths": ["generic input-to-model mapping"]
}
```

### Planner

The Planner returns a schema-valid staged proposal, not an execution result:

```json
{
  "plan_id": "rename-note-001",
  "summary": "Expand, synchronize, backfill, deploy, retire, and contract.",
  "write_consistency": "Both application versions remain consistent during coexistence.",
  "stages": [
    {"id": "expand", "kind": "sql", "phase": "expand", "sql": ["..."]},
    {"id": "backfill", "kind": "sql", "phase": "backfill", "sql": ["..."]},
    {"id": "deploy", "kind": "deploy_new"},
    {"id": "retire", "kind": "retire_old"},
    {"id": "contract", "kind": "sql", "phase": "contract", "sql": ["..."]}
  ]
}
```

The ellipses describe shape only and are not executable SQL or an answer label. The local
validator rejects unknown fields, invalid stage ordering requirements, disallowed SQL, and
missing expand/deploy/retire/contract stages before the runner is called.

### Tester

```json
{
  "rationale": "Exercise cross-version writes and clears relevant to this plan.",
  "checks": ["old_write_new_read"],
  "custom_cases": [
    {
      "id": "cross_version_update",
      "description": "Write through one version and read through the other.",
      "steps": [
        {"app": "old", "op": "write", "user": "A", "value": "updated"},
        {"app": "new", "op": "read", "user": "A"}
      ]
    }
  ]
}
```

### Controller and runner result

Expected terminal verdicts:

| Condition | Verdict |
|---|---|
| Runner completes and all mandatory checks pass | `PASS` |
| Runner completes and checks still fail after allowed revision | `FAIL` |
| Call, token, wall-time, or revision budget prevents completion | `INCOMPLETE` |
| Endpoint, controller, or exhausted-invalid-output failure | `ERROR` |

A successful revision must retain the earlier failed attempt. Agent claims cite runner
observations and never assign their own pass/fail.

## Output files to inspect

Each command creates `runs/<timestamp>-agent-loop-<source>/`:

```text
summary.json                         final verdict, attempts, aggregate checks and model totals
events.jsonl                         append-only checks, stages, proposals, claims and telemetry
agents/review-request.json           normalized input
agents/search_hits.json              deterministic Investigator context
agents/investigator.json             findings
agents/planner-1.json                first plan
agents/tester-propose-1.json         proposed additional checks
agents/tester-report-1.json          evidence summary
agents/transcript-*.json             messages, retries, latency, usage and validation status
plan-<plan-id>.json                  plan accepted by the runner
sql/plan-<plan-id>.sql               generated SQL submitted to guarded execution
```

Check `summary.json` for `agent_totals.model_calls`, token totals, model time, valid-proposal
rate, remaining budget, attempts, and final verdict. Check the `agent_config` event for the
model, checkpoint, vLLM version, hardware, sampling, reasoning, and serving settings.

## Environment setup

### Required platform

Use Linux with Docker, Git, `uv`/Python 3.12, and `libmagic`. Required local ports are:

| Service | Port |
|---|---:|
| PostgreSQL | 55432 |
| Valkey | 56379 |
| Old Saleor app | 8001 |
| New Saleor app | 8002 |

The current Windows workspace cannot run the integration because it has no WSL, Docker,
`uv`, Saleor worktrees, or Saleor virtual environment. Installing only `uv` on Windows would
not make the Linux-oriented Saleor runner valid. Use the team's Linux machine or provision a
Linux environment; do not install system-wide WSL or Docker silently on a teammate's machine.

### Ubuntu preparation

Install the host prerequisites using the team's approved package-management process:

```bash
sudo apt-get update
sudo apt-get install -y git libmagic1 curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install and start Docker Desktop or Docker Engine according to the host policy. Confirm:

```bash
git --version
uv --version
docker version
python3 --version
```

Do not export `UV_PROJECT_ENVIRONMENT`. From the repository root:

```bash
uv sync
uv run migproto setup
uv run migproto baseline --skip-pytest
uv run pytest -q
uv run migproto agent-loop --proposals scripted
```

The first baseline is expected to take about seven minutes. A later baseline is about 50
seconds. The GPU-free scripted loop is about 55 seconds in the recorded environment.

Expected scripted result: attempt 1 fails because the deliberately flawed one-way sync does
not propagate old writes; attempt 2 passes after selecting the staged alternative. The final
verdict is `PASS`, while attempt-1 failures remain in the summary.

## Live Nemotron validation

Provision the GPU only with event-provided NVIDIA Brev credits. Serve the pinned checkpoint
and revision documented in `docs/serving.md` and `docs/bounties.md`; do not use a personal
cloud account or add draft models.

Before running the controller, validate the endpoint:

```bash
curl -s "$MIGPROTO_LLM_BASE_URL/models" \
  -H "Authorization: Bearer $MIGPROTO_LLM_API_KEY"
```

Then run equivalent reviews:

```bash
uv run migproto agent-loop --proposals llm --topology roles
uv run migproto agent-loop --proposals llm --topology single
```

Repeat both topologies under the same request, endpoint, sampling configuration, evidence,
revision limit, and total budget. Record every run separately in `docs/results.md`.

## Models actually used so far

### New implementation validation

No model was called. Tests mocked the OpenAI-compatible vLLM response boundary and verified:

- structured-output payloads and local validation;
- invalid-output retries;
- rejection of empty `message.content` even when reasoning content exists;
- endpoint failure recording;
- shared budget exhaustion returning `INCOMPLETE`;
- invalid plans never reaching the runner;
- both topology configurations and failed-attempt preservation;
- restricted-answer strings absent from rendered prompt constants.

### Historical development run

One `gpt-oss:120b-cloud` run through Ollama exists in
`examples/live-ordering-revision`. It made eight model calls, used 30,036 tokens, and took
88.2 seconds. Attempt 1 failed the retirement gate; attempt 2 reordered retirement and
contract and passed. This is development evidence only, outside the 33B limit, and must not be
mixed with future Nemotron/vLLM results.

### Nemotron

No Nemotron call has been made yet. A qualifying validation requires at least one saved
`roles` run and one saved `single` run using the pinned Lightning checkpoint on the recorded
Brev/vLLM deployment, with complete model, serving, hardware, budget, latency, usage, and
runner-verdict evidence.

## Acceptance checklist

- [ ] Linux, Docker, `uv`, libmagic, PostgreSQL and Valkey setup succeeds.
- [ ] `uv run pytest -q` passes, including PostgreSQL trigger tests.
- [ ] Scripted loop preserves the expected failed first attempt and passing revision.
- [ ] vLLM `/models` and structured chat completion return the served Lightning identity.
- [ ] `message.content` is non-empty and token usage is recorded.
- [ ] Roles run completes with a runner verdict and complete provenance.
- [ ] Single-agent run completes under the identical total budget.
- [ ] Budget exhaustion returns `INCOMPLETE`; endpoint failure returns `ERROR`.
- [ ] Invalid proposals never reach SQL execution.
- [ ] Failed attempts remain visible when a revision passes.
- [ ] Results are copied from saved runs into `docs/results.md` without unsupported claims.
