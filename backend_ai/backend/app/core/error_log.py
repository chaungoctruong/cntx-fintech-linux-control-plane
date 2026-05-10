"""Standardised structured logging helpers.

The goal: every error/warning emitted by the backend should be readable both by
a human on-call and by an AI agent crawling the JSONL logs. A standard record
carries:

  * `event` — stable machine-readable name (snake_case, dot-namespaced),
    e.g. `runner.command.dispatch.failed`. Use these as primary grep keys.
  * `hint` — short human/AI sentence describing where to look or what to do.
    Future Claude/team members read this to know the exact next step.
  * `error_code` — optional stable code for the failure class (machine-readable).
  * `error_class` / `error_message` — auto-extracted from the exception.
  * arbitrary `**context` fields (account_id, deployment_id, runner_id, …) get
    flattened into the JSON record.

The helpers never raise — log failures must never fail the request itself.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional


_AGENT_FIELD_KEYS = ("event", "hint", "error_code", "error_class", "error_message", "operation", "outcome")


def _exc_class(error: BaseException | None) -> str:
    return error.__class__.__name__ if error is not None else ""


def _exc_message(error: BaseException | None, *, max_len: int = 600) -> str:
    if error is None:
        return ""
    raw = str(error) or ""
    return raw if len(raw) <= max_len else raw[:max_len] + "...[truncated]"


def _filter_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    out: dict[str, Any] = {}
    for key, value in context.items():
        if value is None:
            continue
        if key in _AGENT_FIELD_KEYS:
            # Don't let context shadow the canonical agent fields
            continue
        out[str(key)] = value
    return out


def log_agent_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    hint: str | None = None,
    error: BaseException | None = None,
    error_code: str | None = None,
    operation: str | None = None,
    outcome: str | None = None,
    exc_info: bool | None = None,
    context: Mapping[str, Any] | None = None,
    **extra_fields: Any,
) -> None:
    """Emit a single structured log line with agent-friendly fields."""
    try:
        ctx = _filter_context(context)
        ctx.update(_filter_context(extra_fields))
        payload: dict[str, Any] = {"event": str(event)}
        if hint:
            payload["hint"] = str(hint)[:600]
        if error_code:
            payload["error_code"] = str(error_code)[:120]
        if operation:
            payload["operation"] = str(operation)[:120]
        if outcome:
            payload["outcome"] = str(outcome)[:60]
        if error is not None:
            payload["error_class"] = _exc_class(error)
            payload["error_message"] = _exc_message(error)
        payload.update(ctx)

        if exc_info is None and error is not None and level >= logging.ERROR:
            exc_info = True

        # Build a compact text message that still reads well in plain logs.
        message_parts = [str(event)]
        if outcome:
            message_parts.append(f"outcome={outcome}")
        if error is not None:
            message_parts.append(f"error={_exc_class(error)}")
        if hint:
            message_parts.append(f"hint={hint}")
        text = " ".join(message_parts)

        logger.log(level, text, extra=payload, exc_info=exc_info)
    except Exception:
        # Never let logging itself break the caller.
        try:
            logger.log(level, "log_agent_event_failed event=%s", event)
        except Exception:
            pass


def log_agent_failure(
    logger: logging.Logger,
    event: str,
    *,
    error: BaseException,
    hint: str,
    error_code: str | None = None,
    operation: str | None = None,
    context: Mapping[str, Any] | None = None,
    **extra_fields: Any,
) -> None:
    """Convenience: log an ERROR with stack trace + agent fields."""
    log_agent_event(
        logger,
        logging.ERROR,
        event,
        hint=hint,
        error=error,
        error_code=error_code,
        operation=operation,
        outcome="failed",
        exc_info=True,
        context=context,
        **extra_fields,
    )


def log_agent_warning(
    logger: logging.Logger,
    event: str,
    *,
    hint: str,
    error: BaseException | None = None,
    error_code: str | None = None,
    operation: str | None = None,
    context: Mapping[str, Any] | None = None,
    **extra_fields: Any,
) -> None:
    """Convenience: log a WARNING with agent fields."""
    log_agent_event(
        logger,
        logging.WARNING,
        event,
        hint=hint,
        error=error,
        error_code=error_code,
        operation=operation,
        outcome="warning",
        context=context,
        **extra_fields,
    )


def log_agent_info(
    logger: logging.Logger,
    event: str,
    *,
    hint: Optional[str] = None,
    operation: str | None = None,
    outcome: str | None = "ok",
    context: Mapping[str, Any] | None = None,
    **extra_fields: Any,
) -> None:
    """Convenience: log an INFO event."""
    log_agent_event(
        logger,
        logging.INFO,
        event,
        hint=hint,
        operation=operation,
        outcome=outcome,
        context=context,
        **extra_fields,
    )
