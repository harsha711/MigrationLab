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


def migration(doc: str, operations: str) -> str:
    return (f"# EXPERIMENT (manual, not upstream): {doc}\n\n"
            "from django.db import migrations, models\n\n\n"
            "class Migration(migrations.Migration):\n"
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

# scenario id -> variant -> spec. "base_patches" are applied with git am before the edits.
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
}


def git(*args, cwd=SALEOR, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
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
        mig_name, ops = spec["migration"]
        (work / "saleor/account/migrations" / f"{mig_name}.py").write_text(
            migration(spec["subject"], ops))
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
