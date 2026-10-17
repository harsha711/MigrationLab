"""Optional Langfuse tracing for live reviews.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_BASE_URL selects the
region, e.g. https://cloud.langfuse.com or https://us.cloud.langfuse.com;
LANGFUSE_TRACING_ENVIRONMENT, e.g. "dev", separates test traces from measurement runs). When
on, prompts, model replies and reasoning are sent to that Langfuse project. RunLog stays the
evidence record; traces are a viewer and never decide anything. A tracing failure never
fails a review.

One trace per review (session_id = run_id), one generation per model request, one span per
runner attempt. The run directory links to its trace through agents/trace.json.
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


def _exit_quietly(cm, exc: BaseException | None) -> bool:
    """Exit cm; a tracing error is logged, never raised. Returns True if exc was suppressed."""
    try:
        if exc is None:
            cm.__exit__(None, None, None)
            return False
        return bool(cm.__exit__(type(exc), exc, exc.__traceback__))
    except BaseException as err:
        if err is exc:
            return False
        if not isinstance(err, Exception):
            raise
        log.warning("langfuse tracing failed on exit: %s", err)
        return False


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
        if not _exit_quietly(cm, exc):
            raise
    else:
        _exit_quietly(cm, None)


class Review:
    """The review's root observation plus the trace-level attributes propagated under it."""

    def __init__(self, stack: contextlib.ExitStack, span):
        self.stack, self.span = stack, span
        self.trace_id = span.trace_id

    def annotate(self, *, trace_name: str | None = None, tags: list[str] | None = None,
                 metadata: dict | None = None) -> None:
        """Set trace attributes on the root span and on every later observation."""
        try:
            from langfuse import propagate_attributes
            meta = {k: v for k, v in (metadata or {}).items() if v is not None}
            self.stack.enter_context(propagate_attributes(
                trace_name=trace_name, tags=tags, metadata=meta or None))
        except Exception as exc:
            log.warning("langfuse annotate failed: %s", exc)


@contextlib.contextmanager
def review(run_log, source: str):
    """Trace one review. Yields a Review (or None when tracing is off or unavailable)."""
    def factory():
        from langfuse import propagate_attributes
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(propagate_attributes(
                session_id=run_log.run_id, trace_name="review",
                tags=[f"source:{source}"], metadata={"run_id": run_log.run_id}))
            span = stack.enter_context(_get().start_as_current_observation(
                name="review", as_type="agent",
                input={"run_id": run_log.run_id, "source": source}))
        except BaseException:
            stack.close()
            raise
        handle = Review(stack, span)

        class _Ctx:
            def __enter__(self):
                return handle

            def __exit__(self, *exc):
                return stack.__exit__(*exc)
        return _Ctx()

    handle = None
    try:
        with _safe(factory) as handle:
            yield handle
    finally:
        if handle is not None:
            _link(run_log, handle)


def _link(run_log, handle: Review) -> None:
    """Flush, then record where the trace lives (agents/trace.json, not summary.json)."""
    client = _get()
    try:
        client.flush()
    except Exception as exc:
        log.warning("langfuse flush failed: %s", exc)
    url = None
    try:
        url = client.get_trace_url(trace_id=handle.trace_id)
    except Exception as exc:
        log.warning("langfuse trace url unavailable: %s", exc)
    try:
        run_log.write_json("agents/trace.json", {
            "provider": "langfuse",
            "trace_id": handle.trace_id,
            "session_id": run_log.run_id,
            "environment": os.environ.get("LANGFUSE_TRACING_ENVIRONMENT") or "default",
            "url": url,
            "note": "viewer only; events.jsonl and summary.json are the evidence record",
        })
    except Exception as exc:
        log.warning("could not write agents/trace.json: %s", exc)


def configure(handle: Review | None, *, topology: str, model: str | None,
              thinking_budget: int | None, reasoning_enabled: bool | None,
              max_revisions: int, request_id: str | None) -> None:
    """Attach the configuration the review runs with, before any model call."""
    if handle is None:
        return
    handle.annotate(trace_name=f"review {topology}", tags=[f"topology:{topology}"], metadata={
        "topology": topology,
        "model": model,
        "thinking_budget": "unset" if thinking_budget is None else thinking_budget,
        "reasoning_enabled": reasoning_enabled,
        "max_revisions": max_revisions,
        "request_id": request_id,
    })


def finish(handle: Review | None, result: dict) -> None:
    """Record the controller's final verdict (decided by the runner, not by the trace)."""
    if handle is None:
        return
    verdict = result.get("verdict")
    update(handle.span, output={k: result.get(k) for k in (
        "verdict", "reason", "agent_totals")} | {"attempts": [
            {"attempt": a["attempt"], "ok": a["ok"], "failed_checks": a["failed_checks"]}
            for a in result.get("attempts", [])]},
        level="DEFAULT" if verdict == "PASS" else "WARNING",
        status_message=result.get("reason"))
    handle.annotate(tags=[f"verdict:{verdict}"])


def fail(handle: Review | None, exc: BaseException) -> None:
    """The controller raised instead of returning a verdict."""
    if handle is None:
        return
    update(handle.span, level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:500])
    handle.annotate(tags=["verdict:EXCEPTION"])


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
