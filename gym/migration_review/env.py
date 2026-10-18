"""Runner settings for Gym verification, kept apart from the live GPU batches.

Live batches use app ports 8001-8072 and Redis DBs 0-7, so Gym verification uses its own
ports, Redis DB and lock file. `migproto.config` reads these at import time, so `apply()`
must run before anything imports `migproto.config` (directly or through migproto.staged).
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RUNNER_ENV = {
    "MIGPROTO_PORT_OLD": "8081",
    "MIGPROTO_PORT_NEW": "8082",
    "MIGPROTO_CACHE_URL": "redis://127.0.0.1:56379/8",
    "MIGPROTO_LOCK_PATH": str(ROOT / "migproto.lock"),
}


def apply(overrides: dict | None = None) -> dict:
    """Set the Gym runner settings in this process (overriding the shell) and return them."""
    values = {**RUNNER_ENV, **(overrides or {})}
    os.environ.update(values)
    return values
