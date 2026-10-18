import pytest

from migproto.agents import tracing


@pytest.fixture(autouse=True)
def _no_real_langfuse(monkeypatch):
    """Keys exported in a developer shell must never send test reviews to Langfuse."""
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
                 "LANGFUSE_HOST", "LANGFUSE_TRACING_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tracing, "_client", None)
