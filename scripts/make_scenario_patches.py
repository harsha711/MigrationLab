"""Generate the Saleor patch series for the scenario catalogue (migproto/scenarios.py).

Run inside the runner environment (Linux/WSL) from the repo root after `migproto setup`:

    python3 scripts/make_scenario_patches.py            # all scenarios
    python3 scripts/make_scenario_patches.py drop-column

For every scenario variant it creates a throwaway worktree of the pinned Saleor commit, applies
exact text edits (each must match exactly once), writes the Django migration, commits, and
exports `git format-patch pinned..HEAD` into patches/<scenario>/<variant>/. These are manual,
intentionally introduced experiments, not upstream Saleor changes. The rename-note patches
(patches/new-unsafe, patches/new-staged) are not touched.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SALEOR = ROOT / "vendor" / "saleor"
PATCHES = ROOT / "patches"
DEP = '("account", "0106_merge_20260807_0748")'

MODELS = "saleor/account/models.py"
NOTE_LINE = "    note = models.TextField(null=True, blank=True)\n"


def migration(doc: str, operations: str, header: str = "", attrs: str = "") -> str:
    return (f"# EXPERIMENT (manual, not upstream): {doc}\n\n"
            "from django.db import migrations, models\n"
            f"{header}\n\n"
            "class Migration(migrations.Migration):\n"
            f"{attrs}"
            f"    dependencies = [\n        {DEP},\n    ]\n\n"
            f"    operations = [\n{operations}    ]\n")


def state_only(op: str) -> str:
    return ("        migrations.SeparateDatabaseAndState(\n"
            f"            state_operations=[\n{op}            ],\n"
            "            database_operations=[],\n        ),\n")


def indent(op: str, n: int = 4) -> str:
    return "".join(" " * n + line if line.strip() else line for line in op.splitlines(True))


ADD_NICKNAME = ("        migrations.AddField(\n            model_name=\"user\", name=\"nickname\",\n"
                "            field=models.CharField(blank=True, max_length=256, null=True),\n        ),\n")
ADD_OPT_IN = ("        migrations.AddField(\n            model_name=\"user\", name=\"marketing_opt_in\",\n"
              "            field=models.BooleanField(default=False),\n        ),\n")
REMOVE_NOTE = "        migrations.RemoveField(model_name=\"user\", name=\"note\"),\n"
SHORTEN_NOTE = ("        migrations.AlterField(\n            model_name=\"user\", name=\"note\",\n"
                "            field=models.CharField(blank=True, max_length=255, null=True),\n        ),\n")
DB_COLUMN_RENAME = (
    "        migrations.AlterField(\n            model_name=\"user\", name=\"note\",\n"
    "            field=models.TextField(blank=True, db_column=\"note\", null=True),\n        ),\n"
    "        migrations.RenameField(model_name=\"user\", old_name=\"note\", new_name=\"customer_note\"),\n")

DROP_NOTE_EDITS = [
    (MODELS, NOTE_LINE, ""),
    ("saleor/core/utils/anonymization.py", '        "note": fake.paragraph(),\n', ""),
    ("saleor/graphql/account/bulk_mutations/customer_bulk_update.py", '                "note",\n', ""),
    ("saleor/graphql/account/bulk_mutations/customer_bulk_update.py",
     "note_changed = old_instance.note != updated_instance.note",
     "note_changed = False  # User.note removed"),
    ("saleor/graphql/account/mutations/base.py",
     '    note = graphene.String(description="A note about the user.")\n', ""),
    ("saleor/graphql/account/mutations/staff/utils.py", '    "note",\n', ""),
    ("saleor/graphql/account/types.py",
     '    note = PermissionsField(\n        graphene.String,\n'
     '        description="A note about the customer.",\n'
     '        permissions=[AccountPermissions.MANAGE_USERS, AccountPermissions.MANAGE_STAFF],\n'
     '    )\n', ""),
    ("saleor/graphql/account/types.py",
     "    @staticmethod\n    def resolve_note(root: models.User, _info: ResolveInfo):\n"
     "        return root.note\n\n", ""),
]

USER_NAMES = ("    email = models.EmailField(unique=True)\n"
              "    first_name = models.CharField(max_length=256, blank=True)\n"
              "    last_name = models.CharField(max_length=256, blank=True)\n")


def user_last_name(definition: str) -> tuple:
    return (MODELS, USER_NAMES, USER_NAMES.replace(
        "    last_name = models.CharField(max_length=256, blank=True)\n", definition))


def alter_last_name(field: str) -> str:
    return ("        migrations.AlterField(\n            model_name=\"user\", name=\"last_name\",\n"
            f"            field={field},\n        ),\n")


CREATE_TIER = (
    "        migrations.CreateModel(\n            name=\"LoyaltyTier\",\n            fields=[\n"
    "                (\"id\", models.BigAutoField(primary_key=True, serialize=False)),\n"
    "                (\"name\", models.CharField(max_length=100, unique=True)),\n"
    "                (\"created_at\", models.DateTimeField(auto_now_add=True)),\n"
    "            ],\n        ),\n")
TIER_MODEL = ("\n\nclass LoyaltyTier(models.Model):\n"
              "    id = models.BigAutoField(primary_key=True)\n"
              "    name = models.CharField(max_length=100, unique=True)\n"
              "    created_at = models.DateTimeField(auto_now_add=True)\n")
INDEX_ANCHOR = "            *ModelWithMetadata.Meta.indexes,\n            # Orders searching index\n"
ADD_INDEX = ("        AddIndexConcurrently(\n            model_name=\"user\",\n"
             "            index=BTreeIndex(fields=[\"last_name\"], name=\"account_user_last_name_btree\"),\n"
             "        ),\n")
REFERRER_FIELD = ("models.ForeignKey(blank=True, db_column=\"referrer_id\", db_index=False, null=True, "
                  "on_delete=models.SET_NULL, related_name=\"referred_users\", to=\"account.user\")")
ADD_REFERRER = (f"        migrations.AddField(\n            model_name=\"user\", name=\"referrer\",\n"
                f"            field={REFERRER_FIELD},\n        ),\n")
FK_SQL = ("        migrations.RunSQL(\n"
          "            \"ALTER TABLE account_user ADD CONSTRAINT account_user_referrer_fk \"\n"
          "            \"FOREIGN KEY (referrer_id) REFERENCES account_user (id) DEFERRABLE INITIALLY DEFERRED\",\n"
          "            reverse_sql=\"ALTER TABLE account_user DROP CONSTRAINT account_user_referrer_fk\",\n"
          "        ),\n")
ADD_CUSTOMER_NOTE = ("        migrations.AddField(\n            model_name=\"user\", name=\"customer_note\",\n"
                     "            field=models.TextField(blank=True, null=True),\n        ),\n")
OLD_RESOLVER = ("    @staticmethod\n    def resolve_note(root: models.User, _info: ResolveInfo):\n"
                "        return root.{attr}\n")


def raw_resolver(column: str) -> str:
    return ("    @staticmethod\n    def resolve_note(root: models.User, _info: ResolveInfo):\n"
            "        # EXPERIMENT (manual): raw SQL read path; linters that only inspect migrations\n"
            "        # and ORM-level renames do not see this column reference.\n"
            "        from django.db import connection\n\n"
            "        with connection.cursor() as cursor:\n"
            f"            cursor.execute(\"SELECT {column} FROM account_user WHERE id = %s\", [root.pk])\n"
            "            row = cursor.fetchone()\n"
            "        return row[0] if row else None\n")


# --- Tier 3 building blocks ---
EVENT_META = ("        ordering = (\"date\",)\n\n    def __repr__(self):\n"
              "        return f\"{self.__class__.__name__}(type={self.type!r}, user={self.user!r})\"\n")
RENAME_EVENT_TABLE = ("        migrations.AlterModelTable(name=\"customerevent\", "
                      "table=\"account_customer_event\"),\n")
SPLIT_HELPERS = (
    "\n\ndef split_note(note):\n"
    "    \"\"\"EXPERIMENT (manual): 'title: body' -> (title, body); no ': ' -> (None, note).\"\"\"\n"
    "    if note is None:\n        return None, None\n"
    "    i = note.find(\": \")\n"
    "    if i < 0:\n        return None, note\n"
    "    return note[:i], note[i + 2 :]\n\n\n"
    "def compose_note(title, body):\n"
    "    if title is None:\n        return body\n"
    "    return f\"{title}: {body or ''}\"\n")
ADD_TITLE_BODY = (
    "        migrations.AddField(\n            model_name=\"user\", name=\"note_title\",\n"
    "            field=models.TextField(blank=True, null=True),\n        ),\n"
    "        migrations.AddField(\n            model_name=\"user\", name=\"note_body\",\n"
    "            field=models.TextField(blank=True, null=True),\n        ),\n")
SPLIT_RUNPYTHON_HEADER = (
    "\n\ndef split_existing(apps, schema_editor):\n"
    "    User = apps.get_model(\"account\", \"User\")\n"
    "    batch = []\n"
    "    for user in User.objects.exclude(note=None).only(\"id\", \"note\").iterator(chunk_size=2000):\n"
    "        i = user.note.find(\": \")\n"
    "        if i < 0:\n            user.note_title, user.note_body = None, user.note\n"
    "        else:\n            user.note_title, user.note_body = user.note[:i], user.note[i + 2 :]\n"
    "        batch.append(user)\n"
    "        if len(batch) >= 2000:\n"
    "            User.objects.bulk_update(batch, [\"note_title\", \"note_body\"])\n"
    "            batch = []\n"
    "    if batch:\n        User.objects.bulk_update(batch, [\"note_title\", \"note_body\"])\n")
SPLIT_NOTE_EDITS = [
    (MODELS, NOTE_LINE, "    note_title = models.TextField(null=True, blank=True)\n"
                        "    note_body = models.TextField(null=True, blank=True)\n"),
    (MODELS, None, SPLIT_HELPERS),
    ("saleor/core/utils/anonymization.py", '        "note": fake.paragraph(),\n',
     '        "note_body": fake.paragraph(),\n'),
    ("saleor/graphql/account/bulk_mutations/customer_bulk_update.py", '                "note",\n',
     '                "note_title",\n                "note_body",\n'),
    ("saleor/graphql/account/bulk_mutations/customer_bulk_update.py",
     "note_changed = old_instance.note != updated_instance.note",
     "note_changed = (old_instance.note_title, old_instance.note_body) != (\n"
     "                updated_instance.note_title,\n                updated_instance.note_body,\n"
     "            )"),
    ("saleor/graphql/account/bulk_mutations/customer_bulk_update.py",
     "                    old_instance = filtered_customers[0]\n"
     "                    new_instance = cls.construct_instance(deepcopy(old_instance), data)\n",
     "                    old_instance = filtered_customers[0]\n"
     "                    if \"note\" in data:\n"
     "                        from ....account.models import split_note\n\n"
     "                        data[\"note_title\"], data[\"note_body\"] = split_note(data.pop(\"note\"))\n"
     "                    new_instance = cls.construct_instance(deepcopy(old_instance), data)\n"),
    ("saleor/graphql/account/mutations/base.py",
     "        customer_type_id = data.pop(\"customer_type\", None)\n"
     "        cleaned_input = super().clean_input(info, instance, data, **kwargs)\n",
     "        customer_type_id = data.pop(\"customer_type\", None)\n"
     "        cleaned_input = super().clean_input(info, instance, data, **kwargs)\n"
     "        if \"note\" in cleaned_input:\n"
     "            from ....account.models import split_note\n\n"
     "            cleaned_input[\"note_title\"], cleaned_input[\"note_body\"] = split_note(\n"
     "                cleaned_input.pop(\"note\")\n            )\n"),
    ("saleor/graphql/account/mutations/staff/utils.py", '    "note",\n',
     '    "note_title",\n    "note_body",\n'),
    ("saleor/graphql/account/types.py",
     "    @staticmethod\n    def resolve_note(root: models.User, _info: ResolveInfo):\n"
     "        return root.note\n",
     "    @staticmethod\n    def resolve_note(root: models.User, _info: ResolveInfo):\n"
     "        from ...account.models import compose_note\n\n"
     "        return compose_note(root.note_title, root.note_body)\n"),
]
SETTINGS_LANG = "LANGUAGES: list[tuple[str, str]] = CORE_LANGUAGES\n"
LANG_FIELD_OP = ("        migrations.AlterField(\n            model_name=\"user\", name=\"language_code\",\n"
                 "            field=models.CharField(choices=settings.LANGUAGES, "
                 "default=settings.LANGUAGE_CODE, max_length=35),\n        ),\n")
READERS_FIRST_GUARD = (
    "        customer_type_id = data.pop(\"customer_type\", None)\n"
    "        cleaned_input = super().clean_input(info, instance, data, **kwargs)\n",
    "        customer_type_id = data.pop(\"customer_type\", None)\n"
    "        cleaned_input = super().clean_input(info, instance, data, **kwargs)\n"
    "        if cleaned_input.get(\"language_code\") == \"tlh\":\n"
    "            # EXPERIMENT (manual): readers first; tlh is readable but not writable yet.\n"
    "            raise ValidationError(\n                {\n"
    "                    \"language_code\": ValidationError(\n"
    "                        \"Language tlh can be read but not written yet.\",\n"
    "                        code=AccountErrorCode.INVALID.value,\n"
    "                    )\n                }\n            )\n")


# --- Tier 4 building blocks ---
PLAIN_ADD_INDEX = ("        migrations.AddIndex(\n            model_name=\"user\",\n"
                   "            index=BTreeIndex(fields=[\"last_name\"], "
                   "name=\"account_user_last_name_btree\"),\n        ),\n")
ORDERS_LINE = "    number_of_orders = models.PositiveIntegerField(default=0, db_default=0)\n"
ORDERS_BIGINT = ("        migrations.AlterField(\n            model_name=\"user\", name=\"number_of_orders\",\n"
                 "            field=models.PositiveBigIntegerField(db_default=0, default=0),\n        ),\n")
ADD_SCORE = ("        migrations.AddField(\n            model_name=\"user\", name=\"loyalty_score\",\n"
             "            field=models.IntegerField(blank=True, null=True),\n        ),\n")
FILL_SCORES_HEADER = ("\n\ndef fill_scores(apps, schema_editor):\n"
                      "    User = apps.get_model(\"account\", \"User\")\n"
                      "    User.objects.update(loyalty_score=0)\n")


def rename_series(kind: str) -> list[Path]:
    """The original rename patches: kind 'new-unsafe' (RenameField) or 'new-staged' (state-only)."""
    return sorted((PATCHES / kind).glob("*.patch"))


# scenario id -> variant -> spec. "base_patches" are applied with git am before the edits.
# An edit with old=None appends to the file. "migration": None means the base patches already
# provide it (or the version has no migration, like an old version).
SPECS = {
    "add-optional-column": {
        "apply": {"subject": "add optional User.nickname (NULL allowed)",
                  "edits": [(MODELS, NOTE_LINE, NOTE_LINE + "    nickname = models.CharField("
                             "max_length=256, null=True, blank=True)\n")],
                  "migration": ("0107_user_nickname", ADD_NICKNAME)},
    },
    "rename-db-column": {
        "apply": {"subject": "rename User.note attribute to customer_note, keep column via db_column",
                  "base_patches": sorted((PATCHES / "new-staged").glob("0001-*.patch")),
                  "edits": [(MODELS, "    customer_note = models.TextField(null=True, blank=True)\n",
                             "    customer_note = models.TextField(null=True, blank=True, "
                             "db_column=\"note\")\n")],
                  "migration": ("0107_user_note_db_column", DB_COLUMN_RENAME)},
    },
    "add-required-column": {
        v: {"subject": f"add required User.marketing_opt_in ({v})",
            "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                       + "    marketing_opt_in = models.BooleanField(default=False)\n")],
            "migration": ("0107_user_marketing_opt_in",
                          ADD_OPT_IN if v == "apply" else state_only(indent(ADD_OPT_IN, 8)))}
        for v in ("apply", "state-only")
    },
    "drop-column": {
        v: {"subject": f"remove User.note and its GraphQL field and inputs ({v})",
            "edits": DROP_NOTE_EDITS,
            "migration": ("0107_remove_user_note",
                          REMOVE_NOTE if v == "apply" else state_only(indent(REMOVE_NOTE, 8)))}
        for v in ("apply", "state-only")
    },
    "shorten-note": {
        v: {"subject": f"shorten User.note to CharField(max_length=255) ({v})",
            "edits": [(MODELS, NOTE_LINE,
                       "    note = models.CharField(max_length=255, null=True, blank=True)\n")],
            "migration": ("0107_alter_user_note_max_length",
                          SHORTEN_NOTE if v == "apply" else state_only(indent(SHORTEN_NOTE, 8)))}
        for v in ("apply", "state-only")
    },
    # --- Tier 1 (A5 reuses the add-required-column patches) ---
    "add-table": {
        "apply": {"subject": "add model LoyaltyTier (new table)",
                  "edits": [(MODELS, None, TIER_MODEL)],
                  "migration": ("0107_loyaltytier", CREATE_TIER)},
    },
    "add-index-concurrently": {
        "apply": {"subject": "add a B-tree index on User.last_name, built concurrently",
                  "edits": [(MODELS, INDEX_ANCHOR, INDEX_ANCHOR.replace(
                      "            # Orders searching index\n",
                      "            BTreeIndex(fields=[\"last_name\"], "
                      "name=\"account_user_last_name_btree\"),\n"
                      "            # Orders searching index\n"))],
                  "migration": ("0107_user_last_name_btree", ADD_INDEX,
                                "from django.contrib.postgres.indexes import BTreeIndex\n"
                                "from django.contrib.postgres.operations import AddIndexConcurrently\n",
                                "    atomic = False\n\n")},
    },
    "lengthen-varchar": {
        "apply": {"subject": "lengthen User.last_name to varchar(512)",
                  "edits": [user_last_name(
                      "    last_name = models.CharField(max_length=512, blank=True)\n")],
                  "migration": ("0107_alter_user_last_name_512",
                                alter_last_name("models.CharField(blank=True, max_length=512)"))},
    },
    # --- Tier 2 ---
    "unique-last-name": {
        v: {"subject": f"make User.last_name unique ({v})",
            "edits": [user_last_name(
                "    last_name = models.CharField(max_length=256, blank=True, unique=True)\n")],
            "migration": ("0107_alter_user_last_name_unique",
                          (lambda op: op if v == "apply" else state_only(indent(op, 8)))(
                              alter_last_name("models.CharField(blank=True, max_length=256, "
                                              "unique=True)")))}
        for v in ("apply", "state-only")
    },
    "foreign-key-orphans": {
        v: {"subject": f"User.referrer foreign key over legacy referrer_id ({v})",
            "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                       + "    referrer = models.ForeignKey(\n"
                         "        \"self\", null=True, blank=True, on_delete=models.SET_NULL,\n"
                         "        related_name=\"referred_users\", db_column=\"referrer_id\", "
                         "db_index=False,\n    )\n")],
            "migration": ("0107_user_referrer",
                          "        migrations.SeparateDatabaseAndState(\n"
                          f"            state_operations=[\n{indent(ADD_REFERRER, 8)}            ],\n"
                          "            database_operations=["
                          + (f"\n{indent(FK_SQL, 8)}            " if v == "apply" else "")
                          + "],\n        ),\n")}
        for v in ("apply", "state-only")
    },
    "irreversible-data-migration": {
        "apply": {"subject": "add User.customer_note and MOVE notes into it (no reverse)",
                  "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                             + "    customer_note = models.TextField(null=True, blank=True)\n")],
                  "migration": ("0107_move_note",
                                ADD_CUSTOMER_NOTE + "        migrations.RunPython(move_notes),\n",
                                "\n\ndef move_notes(apps, schema_editor):\n"
                                "    User = apps.get_model(\"account\", \"User\")\n"
                                "    User.objects.update(customer_note=models.F(\"note\"), note=None)\n")},
        "reversible": {"subject": "add User.customer_note and COPY notes into it (reversible)",
                       "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                                  + "    customer_note = models.TextField(null=True, blank=True)\n")],
                       "migration": ("0107_move_note",
                                     ADD_CUSTOMER_NOTE
                                     + "        migrations.RunPython(copy_notes, "
                                       "reverse_code=migrations.RunPython.noop),\n",
                                     "\n\ndef copy_notes(apps, schema_editor):\n"
                                     "    User = apps.get_model(\"account\", \"User\")\n"
                                     "    User.objects.update(customer_note=models.F(\"note\"))\n")},
    },
    "raw-sql-rename": {
        "old": {"subject": "old version reads User.note through raw SQL",
                "edits": [("saleor/graphql/account/types.py",
                           OLD_RESOLVER.format(attr="note"), raw_resolver("note"))],
                "migration": None},
        "apply": {"subject": "rename note -> customer_note; raw SQL read updated (RenameField)",
                  "base_patches": rename_series("new-unsafe"),
                  "edits": [("saleor/graphql/account/types.py",
                             OLD_RESOLVER.format(attr="customer_note"), raw_resolver("customer_note"))],
                  "migration": None},
        "state-only": {"subject": "rename note -> customer_note; raw SQL read updated (state-only)",
                       "base_patches": rename_series("new-staged"),
                       "edits": [("saleor/graphql/account/types.py",
                                  OLD_RESOLVER.format(attr="customer_note"),
                                  raw_resolver("customer_note"))],
                       "migration": None},
    },
    # --- Tier 3 ---
    "rename-table": {
        v: {"subject": f"rename CustomerEvent table to account_customer_event ({v})",
            "edits": [(MODELS, EVENT_META, EVENT_META.replace(
                "        ordering = (\"date\",)\n",
                "        ordering = (\"date\",)\n        db_table = \"account_customer_event\"\n"))],
            "migration": ("0107_customerevent_table",
                          RENAME_EVENT_TABLE if v == "apply"
                          else state_only(indent(RENAME_EVENT_TABLE, 8)))}
        for v in ("apply", "state-only")
    },
    "split-note": {
        "apply": {"subject": "split User.note into note_title + note_body (data migration, new app only)",
                  "edits": SPLIT_NOTE_EDITS,
                  "migration": ("0107_split_user_note",
                                ADD_TITLE_BODY + "        migrations.RunPython(split_existing),\n"
                                + state_only(indent(REMOVE_NOTE, 8)),
                                SPLIT_RUNPYTHON_HEADER)},
        "state-only": {"subject": "split User.note into note_title + note_body (state-only)",
                       "edits": SPLIT_NOTE_EDITS,
                       "migration": ("0107_split_user_note",
                                     state_only(indent(ADD_TITLE_BODY + REMOVE_NOTE, 8)))},
    },
    "new-language-value": {
        v: {"subject": f"add language code tlh ({v})",
            "edits": [("saleor/settings.py", SETTINGS_LANG,
                       "LANGUAGES: list[tuple[str, str]] = [*CORE_LANGUAGES, (\"tlh\", \"Klingon\")]\n")]
                     + ([("saleor/graphql/account/mutations/base.py", *READERS_FIRST_GUARD)]
                        if v == "readers-first" else []),
            "migration": ("0107_user_language_code_tlh", LANG_FIELD_OP,
                          "from django.conf import settings\n")}
        for v in ("apply", "readers-first")
    },
    # --- Tier 4 (C1 and C6 reuse add-required-column; C2's safe variant reuses D4) ---
    "plain-index": {
        "apply": {"subject": "add a B-tree index on User.last_name (plain CREATE INDEX)",
                  "edits": [(MODELS, INDEX_ANCHOR, INDEX_ANCHOR.replace(
                      "            # Orders searching index\n",
                      "            BTreeIndex(fields=[\"last_name\"], "
                      "name=\"account_user_last_name_btree\"),\n"
                      "            # Orders searching index\n"))],
                  "migration": ("0107_user_last_name_btree", PLAIN_ADD_INDEX,
                                "from django.contrib.postgres.indexes import BTreeIndex\n")},
    },
    "int-to-bigint": {
        v: {"subject": f"User.number_of_orders -> PositiveBigIntegerField ({v})",
            "edits": [(MODELS, ORDERS_LINE, ORDERS_LINE.replace(
                "PositiveIntegerField", "PositiveBigIntegerField"))],
            "migration": ("0107_user_number_of_orders_bigint",
                          ORDERS_BIGINT if v == "apply" else state_only(indent(ORDERS_BIGINT, 8)))}
        for v in ("apply", "state-only")
    },
    "long-transaction": {
        "state-only": {"subject": "add optional User.nickname (state-only; plan adds the column)",
                       "edits": [(MODELS, NOTE_LINE, NOTE_LINE + "    nickname = models.CharField("
                                  "max_length=256, null=True, blank=True)\n")],
                       "migration": ("0107_user_nickname", state_only(indent(ADD_NICKNAME, 8)))},
    },
    "schema-and-backfill": {
        "apply": {"subject": "add User.loyalty_score and backfill it in the same atomic migration",
                  "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                             + "    loyalty_score = models.IntegerField(null=True, blank=True)\n")],
                  "migration": ("0107_user_loyalty_score",
                                ADD_SCORE + "        migrations.RunPython(fill_scores),\n",
                                FILL_SCORES_HEADER)},
        "state-only": {"subject": "add User.loyalty_score (state-only; plan adds and backfills)",
                       "edits": [(MODELS, NOTE_LINE, NOTE_LINE
                                  + "    loyalty_score = models.IntegerField(null=True, blank=True)\n")],
                       "migration": ("0107_user_loyalty_score", state_only(indent(ADD_SCORE, 8)))},
    },
}


def git(*args, cwd=SALEOR, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def edit(path: Path, old: str | None, new: str) -> None:
    text = path.read_text()
    if old is None:
        path.write_text(text.rstrip("\n") + "\n" + new)
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}:\n{old}")
    path.write_text(text.replace(old, new))


def build(scenario: str, variant: str, spec: dict) -> list[str]:
    name = f"{scenario}-{variant}"
    work = ROOT / "vendor" / f"patchwork-{name}"
    branch = f"patchwork/{name}"
    if work.exists():
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)
    git("branch", "-D", branch, check=False)
    git("worktree", "add", "-q", "-b", branch, str(work), "pinned")
    try:
        ident = ["-c", "user.name=migproto", "-c", "user.email=migproto@local"]
        if spec.get("base_patches"):
            git(*ident, "am", "-q", *map(str, spec["base_patches"]), cwd=work)
        for rel, old, new in spec["edits"]:
            edit(work / rel, old, new)
        if spec["migration"] is not None:
            mig_name, ops, *extra = spec["migration"]
            (work / "saleor/account/migrations" / f"{mig_name}.py").write_text(
                migration(spec["subject"], ops, *extra))
        git("add", "-A", cwd=work)
        git(*ident, "commit", "-q", "-m",
            f"EXPERIMENT (manual, not upstream): {spec['subject']}", cwd=work)
        out = PATCHES / scenario / variant
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        git("format-patch", "-q", "pinned..HEAD", "-o", str(out), cwd=work)
        return sorted(p.name for p in out.glob("*.patch"))
    finally:
        git("worktree", "remove", "--force", str(work), check=False)
        git("branch", "-D", branch, check=False)


def main(argv: list[str]) -> None:
    if not (SALEOR / ".git").exists():
        raise SystemExit("vendor/saleor missing: run `uv run migproto setup` first")
    wanted = argv or list(SPECS)
    for scenario in wanted:
        for variant, spec in SPECS[scenario].items():
            print(f"{scenario}/{variant}: {build(scenario, variant, spec)}")


if __name__ == "__main__":
    main(sys.argv[1:])
