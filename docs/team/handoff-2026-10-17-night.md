# Handoff: night of 2026-10-17 (branch `brev/lightning-serving`)

Owner of the critical path: Harsha (Lightning measurements, merges, results wording). Deadline:
11:00 on 2026-10-18. Read AGENTS.md first; the "Brev Lightning quick start" section covers the GPU.

## State at 20:40 EDT

- **Lightning on Brev:** served on vLLM 0.27.1 on a Brev H100 (`horizontal-jade-beetle`, $3.96/h,
  no stop/start). The deployment is recorded in `docs/serving.md`. About $9.50 of $160 credit
  spent. Only Harsha starts or stops GPU work.
- **Configuration A**, measured on the rename scenario (reasoning cap 16,384, 3 revisions, 300k
  tokens):
  - `roles`: 2 PASS and 3 FAIL.
  - `single`: 7 of 7 INCOMPLETE (token budget).
  - 4 runs hit by a network outage are excluded and labelled infrastructure failures
    (`runs/brev-batch-20261017-191348/INFRA-NOTE`).
  - Every `roles` FAIL ends on the sync trigger's INSERT path.
  - The planner saw only 4 of 13 failures because feedback was truncated. That is fixed in
    `3bdd794` (configuration B, not yet measured).
- **Merged into this branch:**
  - compact feedback;
  - Mahek's scenario catalogue;
  - the agent controller generalised to all 14 scenarios (`agent-loop --scenario`), with a test
    that no prompt contains answers;
  - optional Langfuse tracing (`.env`, environment `brev` for GPU runs, `dev` for tests);
  - parallel reviews (`supervise.py --workers`, `--scenarios`).
- **Checks:** 217 tests pass; the scripted loop passes.
- **Not on `main` yet:** everything above needs PRs. Mahek reviews the runner-file changes
  (`config.py` env overrides, `db.py` clone lock, `cli.py --scenario`).

## Next on the critical path (Harsha)

1. Configuration B on the rename scenario (5+5, 4 workers, traced).
2. Scenario sweep on Lightning (`roles`; D1, D5, A3, A4, A5, B1, B2, B3).
3. `docs/results.md` write-up, AGENTS.md scope, PRs to `main`.

## Open decisions (Harsha)

- **Possible hint:** the A4 prompt states a general Django fact ("columns the running model does
  not know get the DB DEFAULT or NULL on INSERT"). Decide whether to keep it, and disclose it.
- **`recommendation` field:** it is agent-side and stripped before the runner. The alternative is
  adding it to `plans.PLAN_KEYS` (Mahek).

## Tasks for teammates (no GPU, no Brev)

**Rules for every task:**
- Work in your own git worktree from `brev/lightning-serving`.
- Never read or print `.env`.
- Anything that starts Saleor uses `MIGPROTO_PORT_OLD=8081 MIGPROTO_PORT_NEW=8082`,
  `MIGPROTO_CACHE_URL=redis://127.0.0.1:56379/8` and your own `MIGPROTO_LOCK_PATH`.
- Do not edit:
  - `migproto/agents/*`
  - `migproto/staged.py`, `checks.py`, `plans.py`
  - `scripts/brev/*`
- Small commits; open a PR, do not push to shared branches.
- Report facts only.

### T1. Replay UI: compare Brev batches (Owner 3 area, about 2-3 h)

Prompt:
```text
In MigrationLab (read AGENTS.md), extend the Replay UI (migproto/ui/, migproto/memory/query.py)
so a viewer can compare Lightning measurement batches. Inputs are read-only: runs/brev-batch-*/
(runs.jsonl, CONFIG-NOTE, INFRA-NOTE, environment.txt) and each run's summary.json and
agents/trace.json. Add a "Batches" page: per batch, show its configuration note, and per
scenario and topology the counts of PASS / FAIL / INCOMPLETE / ERROR. Runs listed in an
INFRA-NOTE are shown separately as infrastructure failures and are never counted as model
results. Per run, show tokens, model calls, wall time, attempts with failed-check counts, and a
link to the Langfuse trace when agents/trace.json exists. Keep failed attempts visible
(AGENTS.md evidence rule 8). Add tests under tests/memory/ with small fixture batch
directories. Do not modify runs/ or the evidence store's existing records.
```

### T2. Parameter count for the 33B rule (about 1-2 h)

Prompt:
```text
In MigrationLab (read AGENTS.md and docs/model-inventory.md), resolve the open task in
docs/model-inventory.md: count the parameters of
nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 at revision
bee7596271d1495f6992ae224aefde4410e816b8 (and the BF16 reference checkpoint) directly from the
safetensors headers, using HTTP range requests (download only the headers, not the weights).
Sum tensor element counts, unpack NVFP4-packed tensors correctly (two FP4 values per byte;
exclude or separately report scale tensors), and explain the 31.58B (safetensors.total)
versus 32.91B (per-dtype sum) difference. Write the script under scripts/ and the method,
the numbers and the remaining headroom under 33B in docs/model-inventory.md. Do not change
anything else. State clearly what is measured and what is assumed (e.g. MTP layers).
```

### T3. `prior_evidence` same-model filter (about 1 h)

Prompt:
```text
In MigrationLab (read AGENTS.md), migproto/memory/retrieval.py prior_evidence() currently
selects earlier agent runs of the same change regardless of model, so local stand-in runs
(e.g. agent:migproto-qwen3-4b-instruct-24k) would be given to Nemotron runs. Add a required
`model` argument (or derive it from the calling run's provenance) and select only runs whose
provenance is exactly agent:<that model>; also allow restricting to a scenario id now that
review requests carry `scenario`. Report the exclusion counts as today. Add tests in
tests/memory/test_retrieval.py. Do not wire the call into the controller (Harsha does that).
```
