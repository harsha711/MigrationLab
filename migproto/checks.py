"""Deterministic compatibility checks. Expectations are computed here, never by a model."""

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


@dataclass
class Context:
    log: RunLog
    dbname: str
    servers: dict[str, AppServer]
    expected: dict[int, str | None]
    _free_seed: list[int] = field(default_factory=list)

    @classmethod
    def create(cls, log: RunLog, dbname: str, servers: dict[str, AppServer]) -> "Context":
        expected = seed.all_rows(dbname, "note")
        free = [pk for pk, v in seed.seed_rows(dbname, "note").items() if v]
        return cls(log, dbname, servers, expected, free[len(free) // 2:])

    def fresh_seed_user(self) -> int:
        return self._free_seed.pop()


def case_app_version(steps: list[dict]) -> str | None:
    """Apps named by a case definition: 'old', 'new', 'both', or None for no app steps."""
    apps = {s.get("app") for s in steps} & {"old", "new"}
    if len(apps) == 2:
        return "both"
    return next(iter(apps), None)


def run_case(ctx: Context, case_id: str, steps: list[dict], provenance: str = MANUAL) -> bool:
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
        if op == "create":
            email = f"{case_id}-{label}-{uuid.uuid4().hex[:6]}@case.migproto.test".lower()
            res = server.create_customer(email, step.get("value"))
            if res.ok:
                labels[label] = res.value
                ctx.expected[res.value] = step.get("value")
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


def columns_agree(ctx: Context, stage: str) -> bool:
    n, examples = _disagreements(ctx.dbname)
    return ctx.log.check(f"{stage}.columns_agree", n == 0, disagreeing_rows=n, examples=examples,
                         app_version="database", expected={"disagreeing_rows": 0},
                         observed={"disagreeing_rows": n})


def _disagreements(dbname: str) -> tuple[int, list]:
    n = db.query(dbname, "SELECT count(*) FROM account_user "
                         "WHERE note IS DISTINCT FROM customer_note")[0][0]
    rows = db.query(dbname, "SELECT id, note, customer_note FROM account_user "
                            "WHERE note IS DISTINCT FROM customer_note ORDER BY id LIMIT 5")
    return n, [{"id": r[0], "note": r[1], "customer_note": r[2]} for r in rows]


def app_smoke(ctx: Context, role: str, stage: str) -> bool:
    steps = [
        {"app": role, "op": "write", "user": "S", "value": f"smoke {role} @ {stage}"},
        {"app": role, "op": "read", "user": "S"},
    ]
    return run_case(ctx, f"{stage}.{role}_smoke", steps)
