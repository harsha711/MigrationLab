"""Pre-execution dry run of a plan on a throwaway copy of S0, with a scripted-write probe.

A runner-side tool for the agents (never a verdict): it executes the plan's SQL stages in
order, and the deploy_new stage's Django migration, on a fresh clone of the scenario's start
state (seed template + `prepare_sql`), without application servers. It reports, per stage,
whether it executed and the first error. At two points (just before deploy_new, and just
after its migration) it runs scripted single-column UPDATEs on one existing row for each text
column the change names, inside a rolled-back transaction, and reports the value of every
named column afterwards. It does not judge those values; the planner reads them.

Not covered: application servers and their ORM writes, INSERTs, concurrency, the retirement
gate, and every runner check. The clone is dropped afterwards.
"""

from psycopg import sql as pgsql

from . import db, plans, scenarios
from .profile import EXISTING_COLUMN_KEYS
from .saleor_app import manage

NAMED_COLUMN_KEYS = (*EXISTING_COLUMN_KEYS, "new_column")
ERROR_CHARS = 600
MAX_BATCHES = 10_000


def _short(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= ERROR_CHARS else "..." + text[-ERROR_CHARS:]


def _text_columns(dbname: str, table: str, names: list[str]) -> list[str]:
    rows = db.query(dbname, """
        SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE c.relname = %s AND a.attname = ANY(%s) AND a.attnum > 0
          AND NOT a.attisdropped AND t.typcategory = 'S'
        """, (table, names))
    present = {r[0] for r in rows}
    return [n for n in names if n in present]


def _primary_key(dbname: str, table: str) -> str | None:
    rows = db.query(dbname, """
        SELECT a.attname FROM pg_index ix JOIN pg_class c ON c.oid = ix.indrelid
        JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = ANY(ix.indkey)
        WHERE c.relname = %s AND ix.indisprimary
        """, (table,))
    return rows[0][0] if len(rows) == 1 else None


def probe(dbname: str, change: dict, point: str) -> dict:
    """Scripted UPDATEs on one row, one per named text column, each rolled back."""
    table = change.get("table")
    names = list(dict.fromkeys(change[k] for k in NAMED_COLUMN_KEYS if change.get(k)))
    cols = _text_columns(dbname, table, names)
    pk = _primary_key(dbname, table)
    out = {"point": point, "table": table, "columns_present": cols, "writes": []}
    if len(cols) < 2 or pk is None:
        out["skipped"] = "fewer than two of the named text columns exist here"
        return out
    t, p = pgsql.Identifier(table), pgsql.Identifier(pk)
    select = pgsql.SQL("SELECT {} FROM {} WHERE {} = %s").format(
        pgsql.SQL(", ").join(map(pgsql.Identifier, cols)), t, p)
    with db.connect(dbname) as conn:
        conn.execute("SET statement_timeout = '20s'")
        row_id = conn.execute(pgsql.SQL("SELECT {} FROM {} ORDER BY {} LIMIT 1").format(
            p, t, p)).fetchone()
        if row_id is None:
            out["skipped"] = "table has no rows"
            return out
        row_id = row_id[0]
        for i, col in enumerate(cols, 1):
            value = f"preflight probe {i}"
            stmt = f"UPDATE {table} SET {col} = '{value}' WHERE {pk} = <first row>"
            entry = {"write": stmt}
            try:
                with conn.transaction(force_rollback=True):
                    before = dict(zip(cols, conn.execute(select, (row_id,)).fetchone()))
                    conn.execute(pgsql.SQL("UPDATE {} SET {} = %s WHERE {} = %s").format(
                        t, pgsql.Identifier(col), p), (value, row_id))
                    after = dict(zip(cols, conn.execute(select, (row_id,)).fetchone()))
                entry["before"] = {k: _clip(v) for k, v in before.items()}
                entry["after"] = {k: _clip(v) for k, v in after.items()}
            except Exception as exc:
                entry["error"] = _short(str(exc))
            out["writes"].append(entry)
    return out


def _clip(value):
    return value[:80] + "..." if isinstance(value, str) and len(value) > 80 else value


def dry_run(scenario_id: str, plan: dict) -> dict:
    """Dry run of `plan` on a throwaway S0 clone. Never raises for plan problems."""
    sc = scenarios.get(scenario_id)
    report = {"stages": [], "probes": [], "first_error": None}
    try:
        plans.validate_plan(plan)
        variant = sc.variant(plans.deploy_variant(plan))
    except (plans.PlanError, KeyError, StopIteration) as exc:
        report["first_error"] = {"stage": None, "error": _short(str(exc))}
        return report
    dbname = db.new_run_db("preflight")
    try:
        if sc.prepare_sql:
            db.run_guarded(dbname, list(sc.prepare_sql))
        for st in plan["stages"]:
            sid, kind = st["id"], st["kind"]
            entry = {"stage": sid, "kind": kind, "phase": st.get("phase")}
            report["stages"].append(entry)
            if kind == "sql":
                try:
                    if st.get("repeat_until_zero_rows"):
                        for i in range(MAX_BATCHES):
                            batch = db.run_guarded(dbname, st["sql"])
                            if all(r["rowcount"] == 0 for r in batch):
                                break
                        else:
                            raise RuntimeError(f"did not converge in {MAX_BATCHES} batches")
                        entry["batches"] = i + 1
                    else:
                        db.run_guarded(dbname, st["sql"])
                    entry["result"] = "executed"
                except Exception as exc:
                    entry.update(result="error", error=_short(str(exc)))
            elif kind == "deploy_new":
                report["probes"].append(probe(dbname, sc.change, f"before {sid}"))
                mig = manage(variant.version, ["migrate", scenarios.APP_LABEL,
                                               variant.migration], dbname, timeout=300)
                if mig.returncode == 0:
                    entry["result"] = "django migrate exit 0"
                    report["probes"].append(probe(dbname, sc.change, f"after {sid} migration"))
                else:
                    entry.update(result="error", error=_short(mig.stderr or mig.stdout))
            else:
                entry["result"] = "not simulated (application stage)"
            if entry.get("result") == "error":
                report["first_error"] = {"stage": sid, "error": entry["error"]}
                break
    finally:
        db.drop(dbname)
    return report
