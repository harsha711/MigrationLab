# Scenario catalogue results (Olist 10k seed)

Branch `scenarios/catalogue` (based on `seed/olist`, which is based on `runner/verification-v1`). Measured on 2026-10-17 in WSL2 Ubuntu 24.04 with Docker Desktop on one laptop.
- **Data:** `migproto baseline --seed-source olist --seed-limit 10000`, which gives 10,045 `account_user` rows: 10,000 Olist customers plus Saleor's demo users.
  - 4,373 rows have a note.
  - 6 notes are longer than 255 characters.
  - Every run started from snapshot `seed:78c7b909b113d169`.
- **Plans:** scripted (`proposals/scripted/<scenario>/`). Expected verdicts are in `proposals/manual/scenarios/`.
- **Agents:** none were involved. These are runner observations of manually written plans.

**Scope of these results:**
- sequential old/new interleaving only
- one machine
- no concurrency, lock or timing claims
- groups A2, A5 to A8, B2 to B4, C, and D2/D4/D5 of the catalogue were **not** attempted

## Rename regression (refactored runner, `rename-note`)
Identical to the pre-refactor runs on the same seed:

| Run | Result |
|---|---|
| `unsafe` | REPRODUCED 7/7 |
| plan v1 | FAIL: 21 pass / 8 fail / 1 refused (the same 9 checks) |
| plan v2 | PASS 34/34 |
| scripted loop | a1 21/8/1 → a2 34/34 |

`uv run pytest`: 81 passed.

## Matrix: 9 of 9 plans matched their expected verdict and recommendation
| Scenario | Plan | Verdict | Recommendation | Decisive evidence | Run |
|---|---|---|---|---|---|
| D1 add optional column | accept | PASS | accept_as_is | migration `ADD COLUMN "nickname" varchar(256) NULL`; all 8 old/new cross cases pass | `20261017-173548-staged` |
| D3 rename, keep column (`db_column`) | accept | PASS | accept_as_is | `sqlmigrate` prints `(no-op)` for both operations; the column stays `note` | `20261017-173643-staged` |
| A4 required column | unsafe | FAIL | reject | Django runs `ADD COLUMN ... DEFAULT false NOT NULL` and then `DROP DEFAULT`. Old-app inserts fail: `null value in column "marketing_opt_in" ... violates not-null constraint` (`case.old_create_new_read`, tester create case) | `20261017-173734-staged` |
| A4 required column | safe | PASS | staged_rollout | expand `ADD COLUMN ... NOT NULL DEFAULT false`, state-only deploy, contract `DROP DEFAULT` | `20261017-173823-staged` |
| A3 drop column | unsafe | FAIL | reject | `DROP COLUMN "note" CASCADE` while the old app runs. Every old-app operation, creates included, fails with `column account_user.note does not exist` | `20261017-173911-staged` |
| A3 drop column | safe | PASS | staged_rollout | state-only `RemoveField`, retire, then contract `DROP COLUMN note`. 4 cases that need new-app note access are recorded as **skipped**, not passed | `20261017-174005-staged` |
| B1 shorten note | unsafe | FAIL | reject | The migration **succeeds**: `ALTER COLUMN "note" TYPE varchar(255) USING "note"::varchar(255)` silently truncates. `deploy_new.preservation.note`: 6 mismatched (the 6 real reviews over 255 characters). The old app's 300-character write then fails: `value too long for type character varying(255)` | `20261017-174051-staged` |
| B1 shorten note | safe | PASS | staged_rollout | state-only deploy, retire, contract `ADD CONSTRAINT ... CHECK (char_length(note) <= 255) NOT VALID`. No rewrite; all notes preserved | `20261017-174151-staged` |
| B1 shorten note | truncate | FAIL | reject | The expand `UPDATE ... left(note, 255)` alters 6 notes. Preservation fails at expand and deploy, and the contract stage is refused | `20261017-174246-staged` |

## Findings
1. **The tool can now accept a change.** D1 and D3 return `accept_as_is` with the same mandatory checks that reject unsafe plans. Before this, every review ended in a staged rollout or a rejection.
2. **B1 shows why data checks matter.** The naive migration exits 0, so a "migration succeeds and tests pass" gate would let it through. It silently truncated 6 real customer reviews. Only the runner's preservation check caught it, and it caught exactly those 6 rows.
3. **A4 confirms a Django behaviour.** `AddField(default=...)` drops the database default after adding the column, which breaks inserts from the previous version. Keeping a database default during the overlap fixes it.
4. **A3:** Django selects every model column, so dropping a column the new code "doesn't use" breaks all old-app requests on that table, not only those that touch the field.

## Reproduce
```bash
uv run migproto setup                      # builds patches/<scenario>/<variant>/ worktrees
uv run migproto baseline --rebuild-seed --skip-pytest --seed-source olist --olist-dir vendor/olist/raw --seed-limit 10000
uv run migproto staged --scenario <scenario> --plan proposals/scripted/<scenario>/<plan>.json [--keep-db]
```
Scenarios: `add-optional-column`, `rename-db-column`, `add-required-column`, `drop-column`, `shorten-note`, plus the default `rename-note`. Patches are regenerated with `python3 scripts/make_scenario_patches.py`.
