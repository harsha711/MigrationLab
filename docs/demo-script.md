# Demo script (about 6 minutes)

For the presenter. Every number below comes from a recorded run under `runs/` or `examples/`;
nothing is staged live. Fill in the two `TODO` lines once configurations F, G and M finish
(`docs/results.md`).

## Before you start

```bash
uv run migproto ui --port 8765 --db /tmp/demo.sqlite    # scratch DB; no Docker, Postgres or GPU
```

Open `http://127.0.0.1:8765/`. Loading takes a few seconds (about 120 runs). Keep
`docs/results.md` open in a second tab for the tables.

## 1. The problem (45 s): a rename that passes its tests and still breaks production

- Open **Run overview** for `unsafe-rename` (an `examples/` fixture), then its **Matrix**.
- Say: Saleor's `User.note` is renamed to `customer_note` (`account_user.note`). We introduced
  this change ourselves; it is not an upstream bug. Saleor's own account tests pass on both
  versions: 110 of 110.
- Point at the matrix: during a rolling deploy the old app is still running, and its read,
  write and create all fail with `column account_user.note does not exist`.
- Line: "The tests run one version at a time. The bug only exists while two versions share one
  database."

## 2. What we built (30 s)

```text
review request -> investigator -> planner (staged SQL plan) -> tester (extra cases)
   -> runner: fresh database clone, old and new Saleor side by side, every stage executed,
      every cross-version read and write checked
   -> runner evidence back to the planner -> revised plan -> runner verdict
```

- **Model:** Nemotron 3.5 Lightning (`NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`) on vLLM
  0.27.1, on an event-credit Brev H100 (`docs/serving.md`).
- **Authority:** agents propose, the runner disposes. Only the runner produces pass or fail.
  Its mandatory checks (preservation, column agreement, retirement gate) are outside agent
  control.

## 3. A review that fixed itself from evidence (90 s)

Run `20261017-214321-agent-loop-llm` (`roles`, configuration D): **17 → 1 → 0 failed checks,
PASS**, 133,710 tokens.

1. **Run overview:** three attempts, the failed ones included.
2. **Matrix, attempt 1:** old-app writes never reach the new column, and new-app writes never
   reach the old one. There is no two-way sync, so 17 checks fail.
3. **Agents:** the tester's report and the planner's revision notes. Say these are claims; the
   evidence IDs they cite open in the side panel.
4. **Plan, final attempt:** expand (new column), backfill, a sync trigger stage (the stage
   attempt 1 lacked), deploy new, retire old, then contract (drops the trigger). The runner
   refuses a contract stage while the old app still runs.
5. **Matrix, final attempt:** every cross-version case passes.
6. **Metrics:** the model, endpoint, calls, tokens and time.

Caveat to say aloud: this pass covers the checks that were run (245 seeded rows, sequential
interleaving, one machine). It is not a zero-downtime or concurrency guarantee.

## 4. How often it works: the honest numbers (60 s)

Open **Batches**, then `docs/results.md`.

- **Rename, `roles`:** 3 of 14 valid reviews passed across configurations A-E
  (`190133`, `204305`, `214321`).
- **Rename, `single`:** 0 of 20. The configurations differed between topologies (token budget,
  history compaction, the build field), so this is not a multi-agent-versus-single result.
- **Scenario catalogue:** 10 of 13 scenarios matched Mahek's expected verdict (which agents never
  see) in at least one review. One review per scenario per sweep: these are observations, not
  rates.
- Show one failure, `20261017-220028-agent-loop-llm` (1 → 19 → 18 → 1): the runner skipped the
  contract stage because earlier checks failed. A failed plan is never shipped.

## 5. What the evidence taught us (60 s)

Every fix was found in the runner's own records and checked against them before running:
- **Backfills that never finish** were the most common first-attempt failure (`UPDATE ... SET
  customer_note = note WHERE customer_note IS NULL` never reaches 0 rows while some notes are
  NULL). Pre-check backtested on 203 recorded plans: it flags 18 of 18, and 0 of 25 that worked.
- **SQL syntax errors** (`UPDATE ... LIMIT`, `EXECUTE FUNCTION f` without `()`): a check with
  PostgreSQL's own parser flags 18 of 18, and 0 of 181 statements that ran.
- **The planner was misled** by a model-written diagnosis and by a "best attempt" ranking.
  Runner facts now come first, and the planner gets every earlier attempt as plain facts.
- **Memory (configuration M):** the planner gets the runner's failures from earlier reviews of the
  same change by the same model, from our evidence store. It never gets passing plans or answer
  labels.
- TODO: configuration F/G/M rename results (from `docs/results.md`).

## 6. What we do not claim (15 s)

- No zero-downtime, concurrency-safety or general migration support beyond what was tested.
- No superiority of multi-agent over single-agent.
- NeMo Gym: a resources server and frozen tasks exist. TODO: say whether the Lightning
  evaluation ran.
- Bounty qualification is not claimed.

## If something breaks

- **The UI shows no runs:** check that `runs/` holds the batch folders, then restart the UI.
- **Port 8765 is busy:** use `--port 8766`.
- **Fallback:** the tables in `docs/results.md` hold the same numbers.
