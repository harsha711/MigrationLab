"""Migration scenarios the runner can review (manual definitions, provenance: manual).

A scenario names the schema change under review, the new application versions that implement
it, and the runner-side checks that decide pass/fail. Each new version is a git worktree built
from `patches/<version>/` (see setup_env.ensure_worktrees):

- `apply` variant: the naive Django change; deploying it runs its migration's real SQL.
- `state_only` variant: same code, migration wrapped in SeparateDatabaseAndState so the plan's
  SQL stages own the schema change.

`rename-note` is the original User.note -> customer_note scenario; its checks keep their
original, rename-specific form (`rename_hooks`) so existing results do not change.
Scripted plans live in proposals/scripted/<id>/ and expected verdicts in
proposals/manual/scenarios/<id>.json; neither may be shown to evaluated agents.
"""

import re
from dataclasses import dataclass, field

from .config import VENDOR, WORKTREES

APP_LABEL = "account"
TABLE = "account_user"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Variant:
    version: str      # worktree key; patches/<version>/ holds its patch series
    migration: str    # Django migration name in APP_LABEL


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    change: dict
    variants: dict[str, Variant]
    cases: tuple[str, ...]
    preserve: tuple[str, ...] = ("note",)
    not_null: tuple[str, ...] = ()
    absent_after_contract: tuple[str, ...] = ()
    present_after_contract: tuple[str, ...] = ()
    new_app_note_ops: bool = True
    rename_hooks: bool = False
    tags: tuple[str, ...] = field(default=())

    def variant(self, migration: str) -> Variant:
        if migration not in self.variants:
            raise KeyError(f"scenario {self.id} has no {migration!r} variant "
                           f"(has {sorted(self.variants)})")
        return self.variants[migration]


RENAME_CASES = ("old_write_new_read", "new_write_old_read", "old_create_new_read",
                "new_create_old_read", "alternating_same_user", "clear_from_old",
                "clear_from_new", "unicode_new_write_old_read")


def _change(description: str, **columns) -> dict:
    return {"description": description, "table": TABLE, **columns}


SCENARIOS: dict[str, Scenario] = {s.id: s for s in [
    Scenario(
        id="rename-note", title="A1: rename a column (User.note -> customer_note)",
        change=_change("Rename Django field User.note (account_user.note, text NULL) to "
                       "customer_note. Keep the public GraphQL field name note.",
                       old_column="note", new_column="customer_note"),
        variants={"apply": Variant("new-unsafe", "0107_rename_user_note_customer_note"),
                  "state_only": Variant("new-staged", "0107_rename_user_note_customer_note")},
        cases=RENAME_CASES, preserve=("note", "customer_note"), rename_hooks=True,
        tags=("A1",),
    ),
    Scenario(
        id="add-optional-column", title="D1: add an optional column the old app ignores",
        change=_change("Add an optional field User.nickname (varchar, NULL allowed). Old code "
                       "never reads or writes it.", new_column="nickname"),
        variants={"apply": Variant("add-optional-column/apply", "0107_user_nickname")},
        cases=RENAME_CASES, tags=("D1", "safe-control"),
    ),
    Scenario(
        id="rename-db-column", title="D3: rename in code only, keep the database column (db_column)",
        change=_change("Rename the Django attribute User.note to customer_note while keeping the "
                       "database column name note via db_column. Keep GraphQL field note.",
                       old_column="note", new_attribute="customer_note"),
        variants={"apply": Variant("rename-db-column/apply", "0107_user_note_db_column")},
        cases=RENAME_CASES, present_after_contract=("note",), tags=("D3", "safe-control"),
    ),
    Scenario(
        id="add-required-column", title="A4: add a required (NOT NULL) column with a default",
        change=_change("Add a required field User.marketing_opt_in (boolean NOT NULL, Django "
                       "default False).", new_column="marketing_opt_in"),
        variants={"apply": Variant("add-required-column/apply", "0107_user_marketing_opt_in"),
                  "state_only": Variant("add-required-column/state-only",
                                        "0107_user_marketing_opt_in")},
        cases=RENAME_CASES, not_null=("marketing_opt_in",),
        present_after_contract=("marketing_opt_in",), tags=("A4",),
    ),
    Scenario(
        id="drop-column", title="A3: drop a column the new code no longer uses (User.note)",
        change=_change("Remove the Django field User.note and its GraphQL field and inputs; "
                       "the new code no longer reads or writes customer notes.",
                       old_column="note"),
        variants={"apply": Variant("drop-column/apply", "0107_remove_user_note"),
                  "state_only": Variant("drop-column/state-only", "0107_remove_user_note")},
        cases=("old_write_old_read", "old_create_old_read", "new_create_plain_old_read"),
        absent_after_contract=("note",), new_app_note_ops=False, tags=("A3",),
    ),
    Scenario(
        id="shorten-note", title="B1: shorten a text column (User.note -> varchar(255))",
        change=_change("Change User.note from text to CharField(max_length=255).",
                       old_column="note"),
        variants={"apply": Variant("shorten-note/apply", "0107_alter_user_note_max_length"),
                  "state_only": Variant("shorten-note/state-only",
                                        "0107_alter_user_note_max_length")},
        cases=RENAME_CASES + ("old_write_long_note",), present_after_contract=("note",),
        tags=("B1",),
    ),
]}

DEFAULT = "rename-note"

# Register every variant worktree (rename-note's are already in config.WORKTREES).
for _s in SCENARIOS.values():
    for _col in (*_s.preserve, *_s.not_null, *_s.absent_after_contract, *_s.present_after_contract):
        assert _IDENT.match(_col), (_s.id, _col)
    for _v in _s.variants.values():
        WORKTREES.setdefault(_v.version, VENDOR / f"wt-{_v.version.replace('/', '-')}")


def get(scenario_id: str | None) -> Scenario:
    sid = scenario_id or DEFAULT
    if sid not in SCENARIOS:
        raise KeyError(f"unknown scenario {sid!r}; known: {sorted(SCENARIOS)}")
    return SCENARIOS[sid]


def variant_versions() -> list[str]:
    """All new-version worktree keys that setup must build (beyond config.BRANCHES)."""
    return sorted({v.version for s in SCENARIOS.values() for v in s.variants.values()})


def file_safe(version: str) -> str:
    return version.replace("/", "-")
