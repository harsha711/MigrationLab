# Proposal: opt-in Olist seed (for Harsha's review)

**Owner / branch:** Mahek (Owner 3) / `seed/olist`, based on `runner/verification-v1`. It touches runner files (`baseline.py`, `config.py`, `cli.py`), so it needs Harsha's review before merging.

## Why
Today every run uses 200 synthetic notes plus `populatedb` (245 users in total). This adds an **opt-in** seed that loads real customer text from the public Olist dataset:
- about 96k customers
- notes taken from about 40k real Portuguese review comments, with accents, emoji, CRLF and empty values

The goal is to test the `User.note` migration on realistic values and around 100 times the volume. **The default stays exactly as before.**

## Usage
```bash
# download the Kaggle zip manually, unzip into vendor/olist/raw/ (git-ignored)
uv run migproto baseline --rebuild-seed --seed-source olist --olist-dir vendor/olist/raw --seed-limit 10000
uv run migproto baseline --rebuild-seed --seed-source olist --olist-dir vendor/olist/raw   # all ~96k
uv run migproto baseline --rebuild-seed                                                    # back to synthetic
```
`MIGPROTO_OLIST_DIR` can replace `--olist-dir`.

## What changed
- **New `migproto/olist.py`:** reads `olist_customers_dataset.csv`, `olist_orders_dataset.csv` and `olist_order_reviews_dataset.csv` with stdlib `csv`. Creates one customer per `customer_unique_id`:
  - **note:** their review comments (title and message), oldest first, kept verbatim. `None` if the customer wrote no comment.
  - **email:** `olist-<uid>@seed.migproto.test`, so `seed.seed_rows`, preservation and checksums work unchanged.
  - **order:** customers are sorted by `customer_unique_id`, so `--seed-limit N` always selects the same customers.
- **Notes go through a file, not the command line.** `olist.django_seed_script` makes the Saleor shell **read a prepared JSON file** (`vendor/olist/customers-<limit>.json`). `manage.py shell -c` passes the script as a single argv entry, which Linux caps at 128 KiB, and a full Olist seed is well beyond that.
- **`baseline.py`:**
  - `prepare_seed(source, olist_dir, limit)` returns the seed script and its provenance.
  - `seed_ready(source, expected)` now checks the source as well as the count (Olist emails vs synthetic), so switching seeds can't silently reuse the wrong template.
  - New `seed_source` event, and the provenance is added to `seed_reused` and to the baseline summary. It records dataset, license, limit, sha256 of the prepared file, counts, `max_note_chars`, and the number of non-ASCII and multi-line notes.
- **`config.py`:** `OLIST_DIR` (env) and `OLIST_PREPARED_DIR` (`vendor/olist`).
- **`cli.py`:** `baseline --seed-source {synthetic,olist} --olist-dir PATH --seed-limit N`. `all` keeps the defaults.
- **`tests/test_olist.py` (5 tests, no DB):** a hand-written fixture `tests/fixtures/olist-mini` in the Olist CSV format with synthetic text. No real dataset rows are committed.

**Not changed:** checks, plans, the staged and unsafe flows, the evidence rules. The checks already pick any seeded user with a non-empty note and compare against the stored value, so nothing depends on the synthetic note text.

## Licensing
The Olist dataset is **CC BY-NC-SA 4.0**. That's fine for this non-commercial hackathon with attribution. The raw files and prepared JSON stay under git-ignored `vendor/`.

**Watch out:** run events can contain row examples with note values, for instance when columns disagree. An exported fixture from an Olist run would therefore contain dataset text and must carry the attribution and the same license in its `manifest.json`.

## Not yet verified
- The seed has not been loaded into Postgres yet. That needs `setup` plus Docker, which will happen in WSL on Mahek's laptop.
- Timings at 10k and 96k: bulk_create, template clone, backfill batches, preservation over the full table.
- **Wording for results:** call this "real public e-commerce data mapped into Saleor", **not** production data. It doesn't show production traffic, concurrency or other tables.
