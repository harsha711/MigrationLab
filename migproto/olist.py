"""Olist Brazilian E-Commerce customers as an opt-in seed for the Saleor `User.note` scenario.

Source: "Brazilian E-Commerce Public Dataset by Olist" (Kaggle, olistbr/brazilian-ecommerce),
licensed CC BY-NC-SA 4.0. The raw files are downloaded manually and never committed.

Mapping (one Saleor customer per Olist `customer_unique_id`):
- note       = that customer's review comments (title and message), oldest first, joined by
               newlines; None when the customer wrote no comment. Text is kept verbatim
               (Portuguese, accents, emoji, CRLF line breaks), which is the point of the seed.
- last_name  = the customer's city (first one recorded), first_name = "Olist"
- email      = olist-<customer_unique_id>@<SEED_EMAIL_DOMAIN>, so the runner's seed queries
               (`seed.seed_rows`) and preservation checks work unchanged.

Customers are ordered by `customer_unique_id` (a hex hash, so effectively random) and
`limit` takes the first N: the same limit always yields the same customers.
"""

import csv
import hashlib
import json
from pathlib import Path

from .config import SEED_EMAIL_DOMAIN

LICENSE = "CC BY-NC-SA 4.0"
DATASET = "Brazilian E-Commerce Public Dataset by Olist (kaggle.com/datasets/olistbr/brazilian-ecommerce)"
EMAIL_PREFIX = "olist-"
FILES = {
    "customers": ("olist_customers_dataset.csv",
                  {"customer_id", "customer_unique_id", "customer_city"}),
    "orders": ("olist_orders_dataset.csv", {"order_id", "customer_id"}),
    "reviews": ("olist_order_reviews_dataset.csv",
                {"order_id", "review_comment_title", "review_comment_message",
                 "review_creation_date"}),
}


class OlistError(ValueError):
    """The Olist directory is missing files or columns."""


def _rows(directory: Path, key: str):
    name, needed = FILES[key]
    path = directory / name
    if not path.is_file():
        raise OlistError(f"missing {name} in {directory} (download and unzip the Kaggle dataset)")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = needed - set(reader.fieldnames or [])
        if missing:
            raise OlistError(f"{name} lacks columns {sorted(missing)}")
        yield from reader


def _comment(review: dict) -> str | None:
    title = (review["review_comment_title"] or "").strip()
    message = review["review_comment_message"] or ""
    if not message.strip():
        message = ""
    if title and message:
        return f"{title}: {message}"
    return title or message or None


def customers(directory, limit: int | None = None) -> list[dict]:
    """Prepared seed customers: [{"email", "first_name", "last_name", "note"}, ...]."""
    directory = Path(directory)
    unique_of, city_of = {}, {}
    for row in _rows(directory, "customers"):
        uid = row["customer_unique_id"]
        unique_of[row["customer_id"]] = uid
        city_of.setdefault(uid, (row["customer_city"] or "").strip().title())
    customer_of_order = {row["order_id"]: row["customer_id"] for row in _rows(directory, "orders")}

    comments: dict[str, list[tuple[str, str]]] = {}
    for review in _rows(directory, "reviews"):
        text = _comment(review)
        uid = unique_of.get(customer_of_order.get(review["order_id"], ""))
        if text is not None and uid is not None:
            comments.setdefault(uid, []).append((review["review_creation_date"] or "", text))

    selected = sorted(city_of)[:limit] if limit is not None else sorted(city_of)
    out = []
    for uid in selected:
        texts = [t for _, t in sorted(comments.get(uid, []))]
        out.append({"email": f"{EMAIL_PREFIX}{uid}@{SEED_EMAIL_DOMAIN}",
                    "first_name": "Olist", "last_name": city_of[uid][:256],
                    "note": "\n".join(texts) if texts else None})
    return out


def stats(rows: list[dict]) -> dict:
    notes = [r["note"] for r in rows if r["note"] is not None]
    return {
        "customers": len(rows),
        "with_note": len(notes),
        "empty_or_null": len(rows) - len(notes),
        "max_note_chars": max((len(n) for n in notes), default=0),
        "non_ascii_notes": sum(1 for n in notes if not n.isascii()),
        "multiline_notes": sum(1 for n in notes if "\n" in n or "\r" in n),
    }


def prepare(directory, out_path, limit: int | None = None) -> dict:
    """Write the prepared customers JSON; return provenance for the run log."""
    rows = customers(directory, limit)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False)
    out_path.write_text(payload, encoding="utf-8")
    return {"source": "olist", "dataset": DATASET, "license": LICENSE, "limit": limit,
            "path": str(out_path), "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            **stats(rows)}


def django_seed_script(prepared_path) -> str:
    """Saleor shell script that bulk-creates the prepared customers.

    The data is read from a file: `manage.py shell -c` passes the script as one argv entry,
    which Linux caps at 128 KiB, far below a full Olist seed.
    """
    return (
        "import json\n"
        "from saleor.account.models import User\n"
        f"rows = json.load(open({str(prepared_path)!r}, encoding='utf-8'))\n"
        "users = [User(email=r['email'], first_name=r['first_name'], last_name=r['last_name'], "
        "note=r['note'], is_active=True) for r in rows]\n"
        "User.objects.bulk_create(users, batch_size=2000)\n"
        f"print('seeded', User.objects.filter(email__startswith={EMAIL_PREFIX!r}, "
        f"email__endswith='@{SEED_EMAIL_DOMAIN}').count())\n"
    )
