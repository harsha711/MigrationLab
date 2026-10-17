"""Synthetic customers with deterministic notes, created through the old app's ORM."""

import random

from . import db
from .config import SEED_CUSTOMERS, SEED_EMAIL_DOMAIN

SPECIAL_NOTES = [
    "Prefers delivery after 5pm; ring twice.",
    "Préfère la livraison le matin 🚚",
    "日本語のメモ: 配達前に電話してください",
    "Quote test: it's \"VIP\" -- don't drop; SELECT 1;",
    "",
    "Line one\nLine two\n\ttabbed",
    "x" * 2000,
]
WORDS = "loyal wholesale returns fragile gift reseller priority invoice monthly allergy".split()


def synthetic_notes(n: int = SEED_CUSTOMERS, seed: int = 42) -> list[str | None]:
    rng = random.Random(seed)
    notes: list[str | None] = []
    for i in range(n):
        if i % 7 == 3:
            notes.append(None)
        elif i < len(SPECIAL_NOTES) * 3 and i % 3 == 0:
            notes.append(SPECIAL_NOTES[(i // 3) % len(SPECIAL_NOTES)])
        else:
            notes.append(f"seed#{i}: " + " ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 9))))
    return notes


def django_seed_script(notes: list[str | None]) -> str:
    return (
        "from saleor.account.models import User\n"
        f"notes = {notes!r}\n"
        "users = [User(email=f'customer{i:04d}@" + SEED_EMAIL_DOMAIN + "', "
        "first_name='Seed', last_name=f'Customer{i:04d}', note=n, is_active=True) "
        "for i, n in enumerate(notes)]\n"
        "User.objects.bulk_create(users)\n"
        "print('seeded', User.objects.filter(email__endswith='@" + SEED_EMAIL_DOMAIN + "').count())\n"
    )


def seed_rows(dbname: str, column: str) -> dict[int, str | None]:
    if column not in ("note", "customer_note"):
        raise ValueError(column)
    rows = db.query(
        dbname,
        f"SELECT id, {column} FROM account_user WHERE email LIKE %s ORDER BY id",
        (f"%@{SEED_EMAIL_DOMAIN}",),
    )
    return dict(rows)


def all_rows(dbname: str, column: str) -> dict[int, str | None]:
    if column not in ("note", "customer_note"):
        raise ValueError(column)
    return dict(db.query(dbname, f"SELECT id, {column} FROM account_user ORDER BY id"))


def checksum(dbname: str, column: str) -> str:
    if column not in ("note", "customer_note"):
        raise ValueError(column)
    row = db.query(
        dbname,
        f"SELECT md5(coalesce(string_agg(id::text || ':' || coalesce({column}, '<NULL>'), "
        f"'|' ORDER BY id), '')) FROM account_user",
    )
    return row[0][0]
