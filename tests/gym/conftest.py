import sys
from pathlib import Path

import pytest

GYM = Path(__file__).resolve().parents[2] / "gym"
if str(GYM) not in sys.path:
    sys.path.insert(0, str(GYM))


@pytest.fixture(autouse=True)
def _private_lock(monkeypatch, tmp_path):
    """Never touch the repository lock (live batches and the smoke script use real ones)."""
    monkeypatch.setenv("MIGPROTO_LOCK_PATH", str(tmp_path / "test.lock"))
