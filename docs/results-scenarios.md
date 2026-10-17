# Scenario catalogue results (Olist 10k seed)

Branch `scenarios/catalogue` (based on `seed/olist`, which is based on `runner/verification-v1`). Measured on 2026-10-17 in WSL2 Ubuntu 24.04 with Docker Desktop on one laptop.
- **Data:** `migproto baseline --seed-source olist --seed-limit 10000`, which gives 10,045 `account_user` rows: 10,000 Olist customers plus Saleor's demo users.
  - 4,373 rows have a note.
  - 6 notes are longer than 255 characters.
  - Every run started from snapshot `seed:78c7b909b113d169`.
- **Plans:** scripted (`proposals/scripted/<scenario>/`). I wrote the expected verdicts in `proposals/manual/scenarios/` before the runs.
- **Agents:** none were involved. These are runner observations of manually written plans.

**Scope of these results:**
- sequential old/new interleaving only
- one machine
- no concurrency, lock or timing claims
- **not attempted:** A2, A6, A7, and all of group C (C1 to C6)

## Summary
- **14 scenarios (13 new plus the original rename), 22 scripted plans: 22 of 22 matched** their expected verdict and recommendation.
- **Rename regression:** identical before and after the refactor (`unsafe` REPRODUCED 7/7; plan v1 FAIL 21/8/1 with the same 9 checks; plan v2 PASS 34/34; scripted loop a1 fail → a2 pass).
- **`uv run pytest`:** 84 passed.

## Results
| # | Scenario | Plan | Verdict | Recommendation | Decisive evidence |
|---|---|---|---|---|---|
| D1 | add-optional-column | accept | PASS | accept_as_is | `ADD COLUMN "nickname" varchar(256) NULL`; all old/new cross cases pass |
| D2 | add-table | accept | PASS | accept_as_is | `CREATE TABLE "account_loyaltytier" ...`; old app unaffected |
| D3 | rename-db-column | accept | PASS | accept_as_is | `sqlmigrate`: `(no-op)` twice; the column stays `note` |
| D4 | add-index-concurrently | accept | PASS | accept_as_is | non-atomic `CREATE INDEX CONCURRENTLY "account_user_last_name_btree"`; `unchanged.last_name` passes |
| D5 | lengthen-varchar | accept | PASS | accept_as_is | `ALTER COLUMN "last_name" TYPE varchar(512)`; `unchanged.last_name` passes |
| A4 | add-required-column | unsafe | FAIL | reject | Django adds `DEFAULT false NOT NULL`, then `DROP DEFAULT`. Old-app inserts fail: `null value in column "marketing_opt_in" ... violates not-null constraint` |
| A4 | add-required-column | safe | PASS | staged_rollout | expand `ADD COLUMN ... NOT NULL DEFAULT false`, state-only deploy, contract `DROP DEFAULT` |
| A5 | backfill-then-required | unsafe | FAIL | reject | backfill (6 batches), then `SET NOT NULL` before deploy. Old-app inserts fail with the same NOT NULL violation, and the contract stage is refused |
| A5 | backfill-then-required | safe | PASS | staged_rollout | a BEFORE INSERT/UPDATE trigger fills the value for old-app writes; after retirement, re-check, `SET NOT NULL`, drop the trigger |
| A3 | drop-column | unsafe | FAIL | reject | `DROP COLUMN "note" CASCADE` while the old app runs. Every old-app operation fails: `column account_user.note does not exist` |
| A3 | drop-column | safe | PASS | staged_rollout | state-only `RemoveField`, retire, then contract `DROP COLUMN note`. 4 cases that need new-app note access are recorded as **skipped** |
| A8 | raw-sql-rename | unsafe | FAIL | reject | the old app (raw-SQL resolver) fails after `RenameField`. The errors come from Django's ORM (`column account_user.note does not exist`, 44 in the old server log) before the raw query runs, so the runner rejects the plan but **does not isolate the raw-SQL path** |
| A8 | raw-sql-rename | safe | PASS | staged_rollout | same staged rollout as rename plan v2 (two-way sync, backfill, retire, contract) |
| B1 | shorten-note | unsafe | FAIL | reject | the migration **succeeds**: `USING "note"::varchar(255)` silently truncates. `deploy_new.preservation.note`: 6 mismatched (the 6 real reviews over 255 characters). The old app's 300-character write then fails: `value too long for type character varying(255)` |
| B1 | shorten-note | safe | PASS | staged_rollout | contract `CHECK (char_length(note) <= 255) NOT VALID` after retirement; no rewrite, all notes preserved |
| B1 | shorten-note | truncate | FAIL | reject | the truncating `UPDATE` alters 6 notes; preservation fails and the contract is refused |
| B2 | unique-last-name | unsafe | FAIL | reject | `ADD CONSTRAINT ... UNIQUE ("last_name")` fails on duplicate cities; Django migrate exits 1 |
| B2 | unique-last-name | dedupe | FAIL | reject | suffixing duplicates **rewrites customers' last names** (`unchanged.last_name` fails). New customers with an empty last name then collide: `duplicate key value ... Key (last_name)=() already exists`. This change needs a product decision, not a migration pattern |
| B3 | foreign-key-orphans | unsafe | FAIL | reject | validated FK in the migration: `violates foreign key constraint "account_user_referrer_fk"`, `Key (referrer_id)=(1000097) is not present` |
| B3 | foreign-key-orphans | safe | PASS | staged_rollout | `ADD CONSTRAINT ... NOT VALID`, state-only deploy, retire, clear orphans, `VALIDATE CONSTRAINT` |
| B4 | irreversible-data-migration | unsafe | FAIL | reject | the data migration nulls `note` (preservation: **4,364 mismatched**) while the old app runs, and `rollback_check.migration_reversible` fails because the migration has no reverse step |
| B4 | irreversible-data-migration | safe | PASS | accept_as_is | copy, don't move, with `reverse_code`: rollback to `0106` succeeds, old notes are preserved, re-apply succeeds |

The legacy data for B3 (orphan `referrer_id` values) is created by the scenario's `prepare_sql` in each run database only, never in the template.

## Incidents during the runs (reported, not hidden)
1. **WSL2 clock jump → A5 safe infrastructure failure.** In run `20261017-180655-staged` the wall clock jumped **+1,573 s** between t=20.8 s and t=34.0 s. Saleor checks JWT expiry against the wall clock, so the old app's freshly issued token was rejected with `ExpiredSignatureError`, and every old-app call failed before reaching the database.
   - I checked all 24 runs; this was the only jump.
   - **Fix:** `AppServer.gql` now logs in again once when Saleor reports an expired signature (unit-tested).
   - **Rerun with the fix:** PASS (`20261017-185131-staged`).
2. **Runner bug → A8 unsafe ERROR.** The rename checks after deploy assumed both columns exist. An immediate rename has already dropped `note`, so `columns_agree` raised `UndefinedColumn` (run `20261017-184044-staged`, verdict ERROR).
   - **Fix:** only compare columns that exist. Staged rename plans are unaffected (regression unchanged).
   - **Rerun:** FAIL / reject as expected (`20261017-185040-staged`).

## Findings
1. **The reviewer can accept changes.** All five safe controls (D1 to D5) return `accept_as_is`, and so does B4's reversible version, under the same mandatory checks that reject unsafe plans.
2. **Data checks catch what "migration succeeded" misses.**
   - **B1:** silent truncation of 6 real reviews.
   - **B4:** 4,364 notes moved away from under the old app.
   - **B2:** a dedupe that rewrites customer data.

   In each case the migration itself ran without error.
3. **Some changes have no safe migration pattern on this data.** On B2 both plans are rejected. That is an honest outcome; the change needs a product decision.
4. **Limits:**
   - **A8:** the runner observes the outcome, not the code path. Finding the raw-SQL read is the Investigator's job.
   - **Concurrency and locking (group C):** not measured.

## Reproduce
```bash
uv run migproto setup                      # builds patches/<scenario>/<variant>/ worktrees (incl. raw-sql-rename/old)
uv run migproto baseline --rebuild-seed --skip-pytest --seed-source olist --olist-dir vendor/olist/raw --seed-limit 10000
uv run migproto staged --scenario <scenario> --plan proposals/scripted/<scenario>/<plan>.json [--keep-db]
```
Patches are regenerated with `python3 scripts/make_scenario_patches.py`. Scenario definitions are in `migproto/scenarios.py`.
