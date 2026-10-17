"""Deterministic compatibility checks. Expectations are computed here, never by a model."""

import re
import uuid
from dataclasses import dataclass, field

from . import db, seed
from .runlog import MANUAL, RunLog
from .saleor_app import AppServer

# Manually authored catalogue (provenance: manual). Steps use user labels; the
# runner maps each label to a fresh seeded customer or to a customer it creates.
CATALOGUE: dict[str, list[dict]] = {
    "old_write_new_read": [
        {"app": "old", "op": "write", "user": "A", "value": "old wrote this"},
        {"app": "new", "op": "read", "user": "A"},
    ],
    "new_write_old_read": [
        {"app": "new", "op": "write", "user": "A", "value": "new wrote this"},
        {"app": "old", "op": "read", "user": "A"},
    ],
    "old_create_new_read": [
        {"app": "old", "op": "create", "user": "N", "value": "created by old"},
        {"app": "new", "op": "read", "user": "N"},
    ],
    "new_create_old_read": [
        {"app": "new", "op": "create", "user": "N", "value": "created by new"},
        {"app": "old", "op": "read", "user": "N"},
    ],
    "alternating_same_user": [
        {"app": "old", "op": "write", "user": "A", "value": "alt-1 old"},
        {"app": "new", "op": "read", "user": "A"},
        {"app": "new", "op": "write", "user": "A", "value": "alt-2 new"},
        {"app": "old", "op": "read", "user": "A"},
        {"app": "old", "op": "write", "user": "A", "value": "alt-3 old"},
        {"app": "new", "op": "read", "user": "A"},
        {"app": "old", "op": "read", "user": "A"},
    ],
    "clear_from_old": [
        {"app": "old", "op": "clear", "user": "A"},
        {"app": "new", "op": "read", "user": "A"},
    ],
    "clear_from_new": [
        {"app": "new", "op": "clear", "user": "A"},
        {"app": "old", "op": "read", "user": "A"},
    ],
    "unicode_new_write_old_read": [
        {"app": "new", "op": "write", "user": "A", "value": "Ünïcødé ✓ 配達 \"q\" ;--"},
        {"app": "old", "op": "read", "user": "A"},
    ],
}
MANDATORY_AFTER_DEPLOY = list(CATALOGUE)

# Cases used by non-rename scenarios (migproto/scenarios.py). "create_plain" creates a customer
# without sending the note input field (for a new app whose GraphQL input has no `note`); it is
# a runner-internal op, not available to tester proposals (plans.OPS).
CATALOGUE.update({
    "old_write_old_read": [
        {"app": "old", "op": "write", "user": "A", "value": "old wrote this"},
        {"app": "old", "op": "read", "user": "A"},
    ],
    "old_create_old_read": [
        {"app": "old", "op": "create", "user": "N", "value": "created by old"},
        {"app": "old", "op": "read", "user": "N"},
    ],
    "new_create_plain_old_read": [
        {"app": "new", "op": "create_plain", "user": "N"},
        {"app": "old", "op": "read", "user": "N"},
    ],
    "old_write_long_note": [
        {"app": "old", "op": "write", "user": "A", "value": "long note " + "x" * 290},
        {"app": "old", "op": "read", "user": "A"},
    ],
})
NOTE_OPS = {"read", "write", "clear"}


@dataclass
class Context:
    log: RunLog
    dbname: str
    servers: dict[str, AppServer]
    expected: dict[int, str | None]
    _free_seed: list[int] = field(default_factory=list)
    new_app_note_ops: bool = True   # False when the new app has no `note` field (scenario A3)
    baseline: dict = field(default_factory=dict)  # column -> {id: value} at S0 (`unchanged`)

    @classmethod
    def create(cls, log: RunLog, dbname: str, servers: dict[str, AppServer],
               new_app_note_ops: bool = True) -> "Context":
        expected = seed.all_rows(dbname, "note")
        free = [pk for pk, v in seed.seed_rows(dbname, "note").items() if v]
        return cls(log, dbname, servers, expected, free[len(free) // 2:], new_app_note_ops)

    def fresh_seed_user(self) -> int:
        return self._free_seed.pop()


def case_app_version(steps: list[dict]) -> str | None:
    """Apps named by a case definition: 'old', 'new', 'both', or None for no app steps."""
    apps = {s.get("app") for s in steps} & {"old", "new"}
    if len(apps) == 2:
        return "both"
    return next(iter(apps), None)


def run_case(ctx: Context, case_id: str, steps: list[dict], provenance: str = MANUAL) -> bool:
    if not ctx.new_app_note_ops and any(s.get("app") == "new" and s.get("op") in NOTE_OPS
                                        for s in steps):
        # Not applicable, not passed: the new app version has no `note` field to read or write.
        ctx.log.skip(f"case.{case_id}", "new app version has no note field", provenance=provenance,
                     steps=steps, app_version=case_app_version(steps))
        return True
    labels: dict[str, int] = {}
    trace = []
    ok = True
    infra_error = False
    expected_reads, observed_reads = [], []
    failed_step = None
    for i, step in enumerate(steps):
        server = ctx.servers.get(step["app"])
        label, op = step["user"], step["op"]
        entry = dict(step)
        if server is None or not server.alive():
            entry.update(ok=False, error=f"{step['app']} app not running")
            trace.append(entry)
            ok, infra_error, failed_step = False, True, i
            break
        if op in ("create", "create_plain"):
            email = f"{case_id}-{label}-{uuid.uuid4().hex[:6]}@case.migproto.test".lower()
            plain = op == "create_plain"
            res = server.create_customer(email, None if plain else step.get("value"),
                                         include_note=not plain)
            if res.ok:
                labels[label] = res.value
                ctx.expected[res.value] = None if plain else step.get("value")
        else:
            if label not in labels:
                labels[label] = ctx.fresh_seed_user()
            pk = labels[label]
            if op in ("write", "clear"):
                value = None if op == "clear" else step.get("value")
                res = server.write_note(pk, value)
                if res.ok:
                    ctx.expected[pk] = value
            else:
                res = server.read_note(pk)
                want = ctx.expected.get(pk)
                expected_reads.append({"step": i, "user_pk": pk, "value": want})
                observed_reads.append({"step": i, "user_pk": pk,
                                       "value": res.value if res.ok else None})
                if res.ok and res.value != want:
                    res.ok = False
                    res.error = f"read {res.value!r}, expected {want!r}"
        entry.update(user_pk=labels.get(label), ok=res.ok, value=res.value, error=res.error)
        trace.append(entry)
        if not res.ok:
            ok, failed_step = False, i
            break
    ctx.log.check(
        f"case.{case_id}", ok, provenance=provenance, steps=trace,
        app_version=case_app_version(steps), outcome="error" if infra_error else None,
        expected={"all_steps_ok": True, "reads": expected_reads},
        observed={"all_steps_ok": ok, "failed_step": failed_step,
                  "error": trace[failed_step]["error"] if failed_step is not None else None,
                  "reads": observed_reads},
    )
    return ok


def preservation(ctx: Context, column: str, stage: str) -> bool:
    actual = seed.all_rows(ctx.dbname, column)
    missing = [pk for pk in ctx.expected if pk not in actual]
    mismatched = [pk for pk in ctx.expected if pk in actual and actual[pk] != ctx.expected[pk]]
    examples = [{"id": pk, "expected": ctx.expected[pk], "actual": actual.get(pk)}
                for pk in (missing + mismatched)[:5]]
    return ctx.log.check(f"{stage}.preservation.{column}", not missing and not mismatched,
                         rows=len(ctx.expected), missing=len(missing), mismatched=len(mismatched),
                         examples=examples, checksum=seed.checksum(ctx.dbname, column),
                         app_version="database",
                         expected={"column": column, "rows": len(ctx.expected), "missing": 0,
                                   "mismatched": 0},
                         observed={"column": column, "rows": len(actual),
                                   "missing": len(missing), "mismatched": len(mismatched)})


def columns_agree(ctx: Context, stage: str, pair: tuple[str, str] = ("note", "customer_note")) -> bool:
    n, examples = _disagreements(ctx.dbname, pair)
    return ctx.log.check(f"{stage}.columns_agree", n == 0, disagreeing_rows=n, examples=examples,
                         app_version="database", expected={"disagreeing_rows": 0},
                         observed={"disagreeing_rows": n})


def _ident(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"bad column name {name!r}")
    return name


def disagreement_sql(pair: tuple[str, str]) -> tuple[str, str]:
    a, b = map(_ident, pair)
    return (f"SELECT count(*) FROM account_user WHERE {a} IS DISTINCT FROM {b}",
            f"SELECT id, {a}, {b} FROM account_user WHERE {a} IS DISTINCT FROM {b} "
            "ORDER BY id LIMIT 5")


def _disagreements(dbname: str, pair: tuple[str, str] = ("note", "customer_note")) -> tuple[int, list]:
    count_sql, rows_sql = disagreement_sql(pair)
    n = db.query(dbname, count_sql)[0][0]
    rows = db.query(dbname, rows_sql)
    return n, [{"id": r[0], pair[0]: r[1], pair[1]: r[2]} for r in rows]


def null_count_sql(column: str) -> str:
    return f"SELECT count(*) FROM account_user WHERE {_ident(column)} IS NULL"


def not_null(ctx: Context, stage: str, column: str) -> bool:
    """A required column must hold a value in every row, including rows the old app inserted."""
    n = db.query(ctx.dbname, null_count_sql(column))[0][0]
    return ctx.log.check(f"{stage}.not_null.{column}", n == 0, null_rows=n, app_version="database",
                         expected={"column": column, "null_rows": 0},
                         observed={"column": column, "null_rows": n})


def columns_after_contract(ctx: Context, stage: str, present: tuple[str, ...],
                           absent: tuple[str, ...]) -> bool:
    cols = set(db.columns(ctx.dbname, "account_user"))
    missing = [c for c in present if c not in cols]
    lingering = [c for c in absent if c in cols]
    return ctx.log.check(f"{stage}.columns_after_contract", not missing and not lingering,
                         app_version="database",
                         expected={"present": list(present), "absent": list(absent)},
                         observed={"missing": missing, "still_present": lingering})


def snapshot_columns(ctx: Context, columns: tuple[str, ...]) -> None:
    """Remember pre-existing rows' values so `unchanged` can detect rewrites of customer data."""
    ctx.baseline = {col: dict(db.query(ctx.dbname, f"SELECT id, {_ident(col)} FROM account_user"))
                    for col in columns}


def unchanged(ctx: Context, stage: str, column: str) -> bool:
    before = ctx.baseline.get(column, {})
    now = dict(db.query(ctx.dbname, f"SELECT id, {_ident(column)} FROM account_user"))
    changed = [pk for pk, v in before.items() if pk in now and now[pk] != v]
    examples = [{"id": pk, "before": before[pk], "after": now[pk]} for pk in changed[:5]]
    return ctx.log.check(f"{stage}.unchanged.{column}", not changed, changed_rows=len(changed),
                         examples=examples, app_version="database",
                         expected={"column": column, "changed_rows": 0},
                         observed={"column": column, "changed_rows": len(changed),
                                   "rows_compared": len(before)})


def app_smoke(ctx: Context, role: str, stage: str) -> bool:
    if role == "new" and not ctx.new_app_note_ops:
        steps = [{"app": "new", "op": "create_plain", "user": "S"}]
    else:
        steps = [
            {"app": role, "op": "write", "user": "S", "value": f"smoke {role} @ {stage}"},
            {"app": role, "op": "read", "user": "S"},
        ]
    return run_case(ctx, f"{stage}.{role}_smoke", steps)
