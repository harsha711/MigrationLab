"""Disposable database lifecycle and the safety guard for executing SQL."""

import re
import uuid

import psycopg
from psycopg import sql as pgsql

from .config import PG_HOST, PG_PASSWORD, PG_PORT, PG_USER, RUN_DB_PREFIX, SEED_DB

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

DENYLIST = [
    r"\bDROP\s+DATABASE\b",
    r"\bCREATE\s+DATABASE\b",
    r"\bDROP\s+SCHEMA\b",
    r"\bDROP\s+TABLE\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+SYSTEM\b",
    r"\bCOPY\b",
    r"\bCREATE\s+EXTENSION\b",
    r"\b(CREATE|ALTER|DROP)\s+(ROLE|USER)\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bSET\s+ROLE\b",
    r"\bSET\s+SESSION\s+AUTHORIZATION\b",
    r"\bpg_read_file\b",
    r"\bpg_read_binary_file\b",
    r"\blo_import\b",
    r"\blo_export\b",
    r"\bdblink\b",
    r"\bpg_terminate_backend\b",
    r"\bLANGUAGE\s+(?!plpgsql\b|sql\b)\w+",
]


class GuardError(Exception):
    pass


def url(dbname: str) -> str:
    return f"postgres://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{dbname}"


def guard_target(host: str, dbname: str) -> None:
    if host not in ALLOWED_HOSTS:
        raise GuardError(f"refusing non-local database host {host!r}")
    if not dbname.startswith(RUN_DB_PREFIX):
        raise GuardError(
            f"refusing database {dbname!r}: generated SQL may only run on "
            f"disposable '{RUN_DB_PREFIX}*' databases"
        )


def guard_sql(statement: str) -> None:
    for pattern in DENYLIST:
        if re.search(pattern, statement, flags=re.IGNORECASE):
            raise GuardError(f"statement matches denylist pattern {pattern!r}")


def connect(dbname: str, autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(url(dbname), autocommit=autocommit)


def admin() -> psycopg.Connection:
    return connect("postgres")


def exists(dbname: str) -> bool:
    with admin() as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
    return row is not None


def _terminate(conn: psycopg.Connection, dbname: str) -> None:
    conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (dbname,),
    )


def create(dbname: str, template: str | None = None) -> None:
    with admin() as conn:
        if template:
            # Parallel runners clone the same template; serialize the clones.
            conn.execute("SELECT pg_advisory_lock(hashtext('migproto_template_clone'))")
            _terminate(conn, template)
            conn.execute(
                pgsql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    pgsql.Identifier(dbname), pgsql.Identifier(template)
                )
            )
        else:
            conn.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(dbname)))


def drop(dbname: str) -> None:
    with admin() as conn:
        _terminate(conn, dbname)
        conn.execute(pgsql.SQL("DROP DATABASE IF EXISTS {}").format(pgsql.Identifier(dbname)))


def new_run_db(label: str) -> str:
    name = f"{RUN_DB_PREFIX}{label}_{uuid.uuid4().hex[:8]}"
    guard_target(PG_HOST, name)
    create(name, template=SEED_DB)
    return name


def run_guarded(dbname: str, statements: list[str], timeout_s: int = 60) -> list[dict]:
    """Execute untrusted (scripted or model-proposed) SQL on a disposable DB."""
    guard_target(PG_HOST, dbname)
    for statement in statements:
        guard_sql(statement)
    results = []
    with connect(dbname) as conn:
        conn.execute(f"SET statement_timeout = '{int(timeout_s)}s'")
        for statement in statements:
            cur = conn.execute(statement)
            results.append({"sql": statement, "rowcount": cur.rowcount})
    return results


def query(dbname: str, sql: str, params=None) -> list[tuple]:
    with connect(dbname) as conn:
        return conn.execute(sql, params).fetchall()


def columns(dbname: str, table: str) -> list[str]:
    rows = query(
        dbname,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in rows]


def triggers(dbname: str, table: str) -> list[str]:
    rows = query(
        dbname,
        "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE c.relname = %s AND NOT t.tgisinternal ORDER BY tgname",
        (table,),
    )
    return [r[0] for r in rows]
