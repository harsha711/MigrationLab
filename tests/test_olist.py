"""Olist seed preparation (no database). Fixture: tests/fixtures/olist-mini, hand-written in
the Olist CSV format with synthetic text (no real dataset rows are committed)."""

import json
from pathlib import Path

import pytest

from migproto import olist
from migproto.config import SEED_EMAIL_DOMAIN

MINI = Path(__file__).parent / "fixtures" / "olist-mini"


def by_uid(rows):
    return {r["email"].split("@")[0].removeprefix(olist.EMAIL_PREFIX): r for r in rows}


def test_one_customer_per_unique_id_with_review_notes():
    rows = by_uid(olist.customers(MINI))
    assert sorted(rows) == ["aaa1", "bbb2", "ccc3", "ddd4", "eee5"]
    # Two customer_ids share aaa1: both reviews, oldest first, verbatim (newline, accents, emoji).
    assert rows["aaa1"]["note"] == "Ótimo: Produto bom,\nentrega rápida.\nChegou antes do prazo 👍"
    assert rows["aaa1"]["last_name"] == "Sao Paulo"          # first city recorded
    assert rows["bbb2"]["note"] == "Péssimo"                  # title only
    assert rows["ccc3"]["note"] is None                       # whitespace-only message
    assert rows["ddd4"]["note"] is None                       # order without review
    assert rows["eee5"]["note"] is None                       # customer without orders
    assert all(r["email"].endswith("@" + SEED_EMAIL_DOMAIN) for r in rows.values())


def test_limit_is_deterministic_prefix():
    full = olist.customers(MINI)
    assert olist.customers(MINI, limit=2) == full[:2]
    assert [r["email"] for r in full] == sorted(r["email"] for r in full)


def test_prepare_writes_json_and_provenance(tmp_path):
    out = tmp_path / "prepared.json"
    prov = olist.prepare(MINI, out, limit=None)
    assert json.loads(out.read_text(encoding="utf-8")) == olist.customers(MINI)
    assert prov["license"] == "CC BY-NC-SA 4.0" and prov["source"] == "olist"
    assert (prov["customers"], prov["with_note"], prov["empty_or_null"]) == (5, 2, 3)
    assert prov["non_ascii_notes"] == 2 and prov["multiline_notes"] == 1
    assert len(prov["sha256"]) == 64


def test_seed_script_reads_file_not_inline(tmp_path):
    path = tmp_path / "prepared.json"
    script = olist.django_seed_script(path)
    assert repr(str(path)) in script and "bulk_create" in script
    assert len(script.encode()) < 2000      # data never goes through argv (Linux 128 KiB cap)


def test_missing_file_or_column_is_reported(tmp_path):
    with pytest.raises(olist.OlistError, match="olist_customers_dataset.csv"):
        olist.customers(tmp_path)
    for name in ("olist_customers_dataset.csv", "olist_orders_dataset.csv"):
        (tmp_path / name).write_text((MINI / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "olist_order_reviews_dataset.csv").write_text("review_id,order_id\n", encoding="utf-8")
    with pytest.raises(olist.OlistError, match="lacks columns"):
        olist.customers(tmp_path)
