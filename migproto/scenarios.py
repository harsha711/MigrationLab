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
PREVIOUS_MIGRATION = "0106_merge_20260807_0748"   # pinned head; rollback checks migrate back here
ALL_PHASES = ("expand", "backfill", "deploy", "contract")
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
    # Old application version (worktree key). "old" is the pinned, unmodified Saleor; a scenario
    # can ship its own old version when the behaviour under test lives in old code (A8).
    old_version: str = "old"
    # Legacy data created in each run database before the old app starts (manual SQL, run
    # through the guard). Never touches the template.
    prepare_sql: tuple[str, ...] = ()
    # Columns whose values for pre-existing rows must not change during the rollout.
    unchanged: tuple[str, ...] = ()
    # Phases after which `not_null` columns are checked (a column may legitimately hold NULLs
    # right after an expand stage, before its backfill).
    not_null_phases: tuple[str, ...] = ALL_PHASES
    # Values derived from several columns that must equal the expected note per row:
    # (label, SQL expression, columns it needs, phases after which it is checked).
    preserve_exprs: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = ()

    def variant(self, migration: str) -> Variant:
        if migration not in self.variants:
            raise KeyError(f"scenario {self.id} has no {migration!r} variant "
                           f"(has {sorted(self.variants)})")
        return self.variants[migration]


# A6: how the new version presents a split note (must match its GraphQL resolver exactly).
COMPOSED_NOTE_SQL = ("CASE WHEN note_title IS NULL THEN note_body "
                     "ELSE note_title || ': ' || coalesce(note_body, '') END")

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
    # --- Tier 1 -------------------------------------------------------------------------------
    Scenario(
        id="backfill-then-required",
        title="A5: backfill a new column, then make it required while old writers still insert",
        change=_change("Add User.marketing_opt_in (boolean, Django default False) by adding a "
                       "nullable column, backfilling it, and making it NOT NULL.",
                       new_column="marketing_opt_in"),
        variants={"apply": Variant("add-required-column/apply", "0107_user_marketing_opt_in"),
                  "state_only": Variant("add-required-column/state-only",
                                        "0107_user_marketing_opt_in")},
        cases=RENAME_CASES, not_null=("marketing_opt_in",),
        not_null_phases=("backfill", "deploy", "contract"),
        present_after_contract=("marketing_opt_in",), tags=("A5",),
    ),
    Scenario(
        id="add-table", title="D2: add a new table",
        change=_change("Add a new model LoyaltyTier (table account_loyaltytier).",
                       new_table="account_loyaltytier"),
        variants={"apply": Variant("add-table/apply", "0107_loyaltytier")},
        cases=RENAME_CASES, tags=("D2", "safe-control"),
    ),
    Scenario(
        id="add-index-concurrently", title="D4: add an index concurrently",
        change=_change("Add a B-tree index on User.last_name, built with CREATE INDEX "
                       "CONCURRENTLY (non-atomic migration).", indexed_column="last_name"),
        variants={"apply": Variant("add-index-concurrently/apply", "0107_user_last_name_btree")},
        cases=RENAME_CASES, unchanged=("last_name",), tags=("D4", "safe-control"),
    ),
    Scenario(
        id="lengthen-varchar", title="D5: lengthen a varchar column (last_name 256 -> 512)",
        change=_change("Change User.last_name from varchar(256) to varchar(512).",
                       old_column="last_name"),
        variants={"apply": Variant("lengthen-varchar/apply", "0107_alter_user_last_name_512")},
        cases=RENAME_CASES, unchanged=("last_name",), tags=("D5", "safe-control"),
    ),
    # --- Tier 2 -------------------------------------------------------------------------------
    Scenario(
        id="unique-last-name", title="B2: add a unique constraint to a column with duplicates",
        change=_change("Make User.last_name unique.", old_column="last_name"),
        variants={"apply": Variant("unique-last-name/apply", "0107_alter_user_last_name_unique"),
                  "state_only": Variant("unique-last-name/state-only",
                                        "0107_alter_user_last_name_unique")},
        cases=RENAME_CASES, unchanged=("last_name",), tags=("B2",),
    ),
    Scenario(
        id="foreign-key-orphans",
        title="B3: add a foreign key over a legacy column that has orphan values",
        change=_change("Turn the legacy integer column account_user.referrer_id into "
                       "ForeignKey(User) (self-reference).", old_column="referrer_id"),
        variants={"apply": Variant("foreign-key-orphans/apply", "0107_user_referrer"),
                  "state_only": Variant("foreign-key-orphans/state-only", "0107_user_referrer")},
        prepare_sql=(
            "ALTER TABLE account_user ADD COLUMN referrer_id integer",
            "UPDATE account_user SET referrer_id = id - 1 WHERE id % 10 = 0 AND id > 1",
            "UPDATE account_user SET referrer_id = id + 1000000 WHERE id % 97 = 0",
        ),
        cases=RENAME_CASES, present_after_contract=("referrer_id",), tags=("B3",),
    ),
    Scenario(
        id="irreversible-data-migration",
        title="B4: irreversible data migration (move notes into a new column)",
        change=_change("Add User.customer_note and move every note into it with a data "
                       "migration.", old_column="note", new_column="customer_note"),
        variants={"apply": Variant("irreversible-data-migration/apply", "0107_move_note"),
                  "apply-reversible": Variant("irreversible-data-migration/reversible",
                                              "0107_move_note")},
        cases=RENAME_CASES, tags=("B4",),
    ),
    Scenario(
        id="raw-sql-rename",
        title="A8: rename a column that app code reads through raw SQL",
        change=_change("Rename User.note to customer_note. The GraphQL resolver for User.note "
                       "reads the column with a raw SQL query.",
                       old_column="note", new_column="customer_note"),
        old_version="raw-sql-rename/old",
        variants={"apply": Variant("raw-sql-rename/apply", "0107_rename_user_note_customer_note"),
                  "state_only": Variant("raw-sql-rename/state-only",
                                        "0107_rename_user_note_customer_note")},
        cases=RENAME_CASES, preserve=("note", "customer_note"), rename_hooks=True,
        tags=("A8",),
    ),
    # --- Tier 3 -------------------------------------------------------------------------------
    Scenario(
        id="rename-table", title="A2: rename a table (CustomerEvent -> account_customer_event)",
        change=_change("Rename the table of model CustomerEvent from account_customerevent to "
                       "account_customer_event (Meta.db_table).",
                       old_table="account_customerevent", new_table="account_customer_event"),
        variants={"apply": Variant("rename-table/apply", "0107_customerevent_table"),
                  "state_only": Variant("rename-table/state-only", "0107_customerevent_table")},
        cases=RENAME_CASES + ("old_name_write_new_events", "new_name_write_old_events"),
        tags=("A2",),
    ),
    Scenario(
        id="split-note", title="A6: split a column (note -> note_title + note_body)",
        change=_change("Replace User.note with note_title and note_body (split on the first "
                       "': '); GraphQL note becomes title + ': ' + body.",
                       old_column="note", new_columns=["note_title", "note_body"]),
        variants={"apply": Variant("split-note/apply", "0107_split_user_note"),
                  "state_only": Variant("split-note/state-only", "0107_split_user_note")},
        cases=RENAME_CASES,
        preserve_exprs=(("composed_note", COMPOSED_NOTE_SQL, ("note_title", "note_body"),
                         ("backfill", "deploy", "contract")),),
        absent_after_contract=("note",), present_after_contract=("note_title", "note_body"),
        tags=("A6",),
    ),
    Scenario(
        id="new-language-value", title="A7: add a new allowed value (language code tlh)",
        change=_change("Add the language code tlh (Klingon) to settings.LANGUAGES, so "
                       "LanguageCodeEnum and User.language_code accept it.",
                       column="language_code", new_value="tlh"),
        variants={"apply": Variant("new-language-value/apply", "0107_user_language_code_tlh"),
                  "apply-readers-first": Variant("new-language-value/readers-first",
                                                 "0107_user_language_code_tlh")},
        cases=RENAME_CASES + ("new_lang_write_old_read",), tags=("A7",),
    ),
]}

DEFAULT = "rename-note"

# Register every variant worktree (rename-note's are already in config.WORKTREES).
for _s in SCENARIOS.values():
    for _col in (*_s.preserve, *_s.not_null, *_s.absent_after_contract, *_s.present_after_contract,
                 *_s.unchanged):
        assert _IDENT.match(_col), (_s.id, _col)
    for _version in (*(v.version for v in _s.variants.values()), _s.old_version):
        WORKTREES.setdefault(_version, VENDOR / f"wt-{_version.replace('/', '-')}")


def get(scenario_id: str | None) -> Scenario:
    sid = scenario_id or DEFAULT
    if sid not in SCENARIOS:
        raise KeyError(f"unknown scenario {sid!r}; known: {sorted(SCENARIOS)}")
    return SCENARIOS[sid]


def variant_versions() -> list[str]:
    """All new-version worktree keys that setup must build (beyond config.BRANCHES)."""
    versions = {v.version for s in SCENARIOS.values() for v in s.variants.values()}
    versions |= {s.old_version for s in SCENARIOS.values() if s.old_version != "old"}
    return sorted(versions)


def file_safe(version: str) -> str:
    return version.replace("/", "-")
