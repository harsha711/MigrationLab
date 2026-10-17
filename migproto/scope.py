"""Deterministic scope facts for observations: app identity, schema state, start snapshot."""

import hashlib
import subprocess
from functools import cache

from . import db
from .config import PATCHES, SALEOR_COMMIT, WORKTREES


@cache
def worktree_head(version: str) -> str | None:
    """HEAD of a worktree. Patched versions are re-created by `git am`, so this differs
    between machines; pair it with patch_sha256 to identify the version."""
    try:
        proc = subprocess.run(["git", "-C", str(WORKTREES[version]), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, KeyError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


@cache
def patch_sha256(version: str) -> str | None:
    files = sorted((PATCHES / version).glob("*.patch"))
    if not files:
        return None
    h = hashlib.sha256()
    for path in files:
        h.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def app_identity(version: str) -> dict:
    return {"worktree": version, "commit": worktree_head(version),
            "pinned_commit": SALEOR_COMMIT, "patch_sha256": patch_sha256(version)}


def schema_state(dbname: str, table: str = "account_user") -> dict:
    cols = db.query(dbname, "SELECT column_name, data_type, is_nullable "
                            "FROM information_schema.columns WHERE table_name = %s "
                            "ORDER BY ordinal_position", (table,))
    trig = db.query(dbname, "SELECT t.tgname, pg_get_triggerdef(t.oid), p.prosrc "
                            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_proc p ON p.oid = t.tgfoid "
                            "WHERE c.relname = %s AND NOT t.tgisinternal ORDER BY t.tgname",
                    (table,))
    mig = db.query(dbname, "SELECT name FROM django_migrations WHERE app = 'account' "
                           "ORDER BY name DESC LIMIT 1")
    h = hashlib.sha256(repr((cols, trig)).encode()).hexdigest()[:16]
    return {
        "table": table,
        "note_columns": [c[0] for c in cols if "note" in c[0]],
        "triggers": [t[0] for t in trig],
        "account_migration": mig[0][0] if mig else None,
        "schema_hash": h,
    }


def snapshot_id(dbname: str, state: dict) -> str:
    """Identity of a database's starting contents: note values plus the table's schema hash."""
    note = db.query(dbname, "SELECT md5(coalesce(string_agg(id::text || ':' || "
                            "coalesce(note, '<NULL>'), '|' ORDER BY id), '')), count(*) "
                            "FROM account_user")[0]
    digest = hashlib.sha256(f"{note[0]}|{note[1]}|{state['schema_hash']}".encode()).hexdigest()
    return f"seed:{digest[:16]}"
