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

SEED_DB = "migproto_seed"
RUN_DB_PREFIX = "migproto_run_"

PORTS = {"old": 8001, "new": 8002}
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin"

SEED_CUSTOMERS = 200
SEED_EMAIL_DOMAIN = "seed.migproto.test"
