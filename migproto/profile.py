"""Read-only schema and data profile of a scenario's starting database (S0).

The same generic profile for every scenario: for each existing column the change names, its
type, nullability, default, and value statistics, plus the constraints and indexes that
involve it and the table's row count. It never states scenario-specific findings ("there are
duplicates"); the planner reads the numbers like a reviewer running `\\d` and a few counts.

The profile is taken on a throwaway clone of the seed template with the scenario's
`prepare_sql` applied (the same start state `staged.run` builds), and the clone is dropped.
"""

from psycopg import sql as pgsql

from . import db, scenarios

# Change keys that may name an existing column (new_column / new_attribute / new_table do not
# exist before the change).
EXISTING_COLUMN_KEYS = ("old_column", "indexed_column")
TEXT_TYPES = ("text", "character varying", "character")


def _column(dbname: str, table: str, column: str) -> dict | None:
    rows = db.query(dbname, """
        SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid), t.typcategory
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_type t ON t.oid = a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE c.relname = %s AND a.attname = %s AND a.attnum > 0 AND NOT a.attisdropped
        """, (table, column))
    if not rows:
        return None
    type_, not_null, default, category = rows[0]
    ident = pgsql.Identifier(column)
    stats = pgsql.SQL("SELECT count(*), count({c}), count(DISTINCT {c}) FROM {t}").format(
        c=ident, t=pgsql.Identifier(table))
    with db.connect(dbname) as conn:
        total, non_null, distinct = conn.execute(stats).fetchone()
        max_len = None
        if category == "S":
            max_len = conn.execute(pgsql.SQL("SELECT max(char_length({c})) FROM {t}").format(
                c=ident, t=pgsql.Identifier(table))).fetchone()[0]
    out = {"column": column, "type": type_, "not_null": bool(not_null), "default": default,
           "rows": total, "null_values": total - non_null, "distinct_non_null_values": distinct}
    if category == "S":
        out["max_char_length"] = max_len
    return out


def _constraints(dbname: str, table: str, columns: list[str]) -> list[str]:
    rows = db.query(dbname, """
        SELECT con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
        WHERE c.relname = %s AND EXISTS (
            SELECT 1 FROM pg_attribute a WHERE a.attrelid = con.conrelid
              AND a.attnum = ANY(con.conkey) AND a.attname = ANY(%s))
        ORDER BY con.conname
        """, (table, columns))
    return [f"{name}: {definition}" for name, definition in rows]


def _indexes(dbname: str, table: str, columns: list[str]) -> list[str]:
    rows = db.query(dbname, """
        SELECT i.relname, pg_get_indexdef(ix.indexrelid)
        FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class c ON c.oid = ix.indrelid
        WHERE c.relname = %s AND EXISTS (
            SELECT 1 FROM pg_attribute a WHERE a.attrelid = ix.indrelid
              AND a.attnum = ANY(ix.indkey) AND a.attname = ANY(%s))
        ORDER BY i.relname
        """, (table, columns))
    return [definition for _, definition in rows]


def profile_db(dbname: str, change: dict) -> dict:
    """Profile of `change['table']` and the existing columns the change names, in `dbname`."""
    table = change.get("table")
    names = [change[k] for k in EXISTING_COLUMN_KEYS if change.get(k)]
    cols = [c for c in (_column(dbname, table, n) for n in dict.fromkeys(names)) if c]
    present = [c["column"] for c in cols]
    with db.connect(dbname) as conn:
        rows = conn.execute(pgsql.SQL("SELECT count(*) FROM {}").format(
            pgsql.Identifier(table))).fetchone()[0]
    return {"table": table, "rows": rows, "columns": cols,
            "constraints": _constraints(dbname, table, present) if present else [],
            "indexes": _indexes(dbname, table, present) if present else []}


def scenario_profile(scenario_id: str, change: dict) -> dict:
    """Profile of the scenario's S0 on a throwaway clone (seed template + prepare_sql)."""
    sc = scenarios.get(scenario_id)
    dbname = db.new_run_db("profile")
    try:
        if sc.prepare_sql:
            db.run_guarded(dbname, list(sc.prepare_sql))
        return profile_db(dbname, change)
    finally:
        db.drop(dbname)


def render(profile: dict) -> str:
    """Plain-text block for the planner and tester prompts."""
    lines = [f"Starting database (S0, measured read-only before planning): table "
             f"{profile['table']} has {profile['rows']} rows."]
    for c in profile["columns"]:
        parts = [f"type {c['type']}", "NOT NULL" if c["not_null"] else "NULL allowed",
                 f"default {c['default']}" if c["default"] is not None else "no default",
                 f"{c['null_values']} NULL values",
                 f"{c['distinct_non_null_values']} distinct non-NULL values"]
        if "max_char_length" in c:
            parts.append(f"longest value {c['max_char_length']} characters"
                         if c["max_char_length"] is not None else "longest value: none")
        lines.append(f"- Column {c['column']}: " + ", ".join(parts) + ".")
    if profile["columns"]:
        lines.append("- Constraints on these columns: "
                     + ("; ".join(profile["constraints"]) or "none") + ".")
        lines.append("- Indexes on these columns: "
                     + ("; ".join(profile["indexes"]) or "none") + ".")
    return "\n".join(lines) + "\n"
