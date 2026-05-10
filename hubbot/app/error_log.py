"""Hubbot mirror of `app.core.error_log` (backend). Stdlib only, never raises."""
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
        if value is None or key in _AGENT_FIELD_KEYS:
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
