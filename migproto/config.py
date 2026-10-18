import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
RUNS = ROOT / "runs"
PATCHES = ROOT / "patches"
PROPOSALS = ROOT / "proposals"

SALEOR_REPO = "https://github.com/saleor/saleor"
SALEOR_COMMIT = "0c0e2697cc72aa999f7786ae8aeda7665bd4285f"
SALEOR_CLONE = VENDOR / "saleor"
VENV = VENDOR / "venv"
VENV_PY = VENV / "bin" / "python"

WORKTREES = {
    "old": VENDOR / "wt-old",
    "new-unsafe": VENDOR / "wt-new-unsafe",
    "new-staged": VENDOR / "wt-new-staged",
}
BRANCHES = {"new-unsafe": "exp/new-unsafe", "new-staged": "exp/new-staged"}

PG_HOST = "127.0.0.1"
PG_PORT = 55432
PG_USER = "saleor"
PG_PASSWORD = "saleor"
CACHE_URL = "redis://127.0.0.1:56379/0"

# Template database every run clones. MIGPROTO_SEED_DB selects another template (for example
# migproto_seed_scale for the large-table scenarios) without touching the default one.
SEED_DB = os.environ.get("MIGPROTO_SEED_DB", "migproto_seed")
if not SEED_DB.startswith("migproto_seed") or not SEED_DB.replace("_", "").isalnum():
    raise ValueError(f"MIGPROTO_SEED_DB must look like migproto_seed[_name], got {SEED_DB!r}")
RUN_DB_PREFIX = "migproto_run_"

PORTS = {"old": 8001, "new": 8002}
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin"

SEED_CUSTOMERS = 200
SEED_EMAIL_DOMAIN = "seed.migproto.test"
# Opt-in Olist seed (`baseline --seed-source olist`). Raw Kaggle files are downloaded
# manually; the prepared JSON lives under vendor/ (git-ignored, CC BY-NC-SA 4.0 data).
OLIST_DIR = os.environ.get("MIGPROTO_OLIST_DIR")
OLIST_PREPARED_DIR = VENDOR / "olist"
