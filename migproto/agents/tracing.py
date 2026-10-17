"""Optional Langfuse tracing for live reviews.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_HOST selects the
region, e.g. https://cloud.langfuse.com or https://us.cloud.langfuse.com). When on, prompts,
model replies and reasoning are sent to that Langfuse project. RunLog stays the evidence
record; traces are a viewer and never decide anything. A tracing failure never fails a review.

One trace per review (session_id = run_id), one generation per model request, one span per
runner attempt.
"""

import contextlib
import logging
import os

log = logging.getLogger(__name__)
_client = None


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _get():
    global _client
    if _client is None:
        from langfuse import get_client
        _client = get_client()
    return _client


@contextlib.contextmanager
def _safe(factory):
    """Enter factory() if tracing is on and works; otherwise yield None."""
    if not enabled():
        yield None
        return
    try:
        cm = factory()
        obs = cm.__enter__()
    except Exception as exc:  # tracing must never break a review
        log.warning("langfuse tracing disabled for this observation: %s", exc)
        yield None
        return
    try:
        yield obs
    except BaseException as exc:
        if not cm.__exit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        cm.__exit__(None, None, None)


@contextlib.contextmanager
def review(run_id: str, source: str, topology: str):
    def factory():
        from langfuse import propagate_attributes
        stack = contextlib.ExitStack()
        stack.enter_context(propagate_attributes(
            session_id=run_id, trace_name=f"review {topology}",
            tags=[f"topology:{topology}", f"source:{source}"], metadata={"run_id": run_id}))
        span = stack.enter_context(_get().start_as_current_observation(
            name="review", as_type="agent",
            input={"run_id": run_id, "source": source, "topology": topology}))

        class _Ctx:
            def __enter__(self):
                return span

            def __exit__(self, *exc):
                return stack.__exit__(*exc)
        return _Ctx()

    with _safe(factory) as span:
        try:
            yield span
        finally:
            if span is not None:
                try:
                    _get().flush()
                except Exception as exc:
                    log.warning("langfuse flush failed: %s", exc)


def generation(name: str, model: str, params: dict, messages: list):
    return _safe(lambda: _get().start_as_current_observation(
        name=name, as_type="generation", model=model, model_parameters=params, input=messages))


def attempt(attempt_no: int, result: dict, failed_checks: list[str]):
    """Record one runner attempt (the runner's verdict, not the model's)."""
    with _safe(lambda: _get().start_as_current_observation(
            name=f"runner attempt {attempt_no}", as_type="tool",
            input={"plan_id": result.get("plan_id")},
            output={"ok": result.get("ok"), "halted": result.get("halted"),
                    "failed_checks": failed_checks},
            level="DEFAULT" if result.get("ok") else "WARNING")):
        pass


def update(obs, **fields) -> None:
    if obs is None:
        return
    try:
        obs.update(**fields)
    except Exception as exc:
        log.warning("langfuse update failed: %s", exc)
