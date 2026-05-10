"""HTTP request logging middleware.

Wraps every HTTP request to:
  * generate or honour an inbound X-Request-ID
  * stash it (and any caller-provided context) in contextvars so all downstream
    log lines emitted during the request inherit `request_id`
  * emit a single structured `request.end` log line with method, path, status,
    elapsed_ms — written via the standard logger, so it lands in both the text
    and the JSONL sinks
  * echo the request id back on the response

Skips noisy infra paths (`/health`, `/ready`, static asset prefixes) to keep
the log volume sane.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.log_context import bind_log_context, new_request_id, reset_log_context


_log = logging.getLogger("api.request")

_DEFAULT_SKIP_EXACT = frozenset({
    "/health",
    "/ready",
    "/api/v2/system/healthz",
    "/favicon.ico",
})

_DEFAULT_SKIP_PREFIXES = (
    "/_next/",
    "/static/",
)

# Endpoints that are pure liveness/long-poll. Demoted to DEBUG so they don't
# drown the INFO sink. Real state lives elsewhere:
#   - heartbeat: tracked separately via `runner.event.ingest` for actual events
#     and via the existing access-log noise filter
#   - commands/claim: 10s long-poll, runners hit constantly
# Errors (5xx/4xx) on these paths are still WARN/ERROR — only success demoted.
_DEFAULT_DEBUG_EXACT = frozenset({
    "/api/v2/runner/commands/claim",
    "/api/v2/runner/heartbeat",
})

_DEFAULT_DEBUG_PREFIXES: tuple[str, ...] = ()


def _slow_request_threshold_ms() -> float:
    raw = os.getenv("SLOW_REQUEST_MS_THRESHOLD", "1500").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1500.0


class RequestContextMiddleware:
    """Pure-ASGI request context + access-log middleware.

    Pure ASGI is used (rather than BaseHTTPMiddleware) to avoid the response-body
    proxying overhead of BaseHTTPMiddleware on every request.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        skip_exact: Iterable[str] | None = None,
        skip_prefixes: Iterable[str] | None = None,
        debug_exact: Iterable[str] | None = None,
        debug_prefixes: Iterable[str] | None = None,
    ) -> None:
        self._app = app
        self._skip_exact = frozenset(skip_exact) if skip_exact is not None else _DEFAULT_SKIP_EXACT
        self._skip_prefixes = tuple(skip_prefixes) if skip_prefixes is not None else _DEFAULT_SKIP_PREFIXES
        self._debug_exact = frozenset(debug_exact) if debug_exact is not None else _DEFAULT_DEBUG_EXACT
        self._debug_prefixes = tuple(debug_prefixes) if debug_prefixes is not None else _DEFAULT_DEBUG_PREFIXES
        self._slow_threshold_ms = _slow_request_threshold_ms()

    def _should_skip(self, path: str) -> bool:
        if path in self._skip_exact:
            return True
        return any(path.startswith(prefix) for prefix in self._skip_prefixes)

    def _should_demote_to_debug(self, path: str) -> bool:
        if path in self._debug_exact:
            return True
        return any(path.startswith(prefix) for prefix in self._debug_prefixes)

    @staticmethod
    def _read_request_id(scope: Scope) -> str:
        for header_name, header_value in scope.get("headers") or []:
            if header_name == b"x-request-id":
                try:
                    decoded = header_value.decode("latin-1").strip()
                except Exception:
                    decoded = ""
                if decoded:
                    return decoded[:64]
        return ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        skip_log = self._should_skip(path)

        request_id = self._read_request_id(scope) or new_request_id()
        bind_log_context(request_id=request_id)

        client_addr = ""
        client = scope.get("client") or ()
        if client:
            try:
                client_addr = str(client[0])
            except Exception:
                client_addr = ""

        status_holder = {"code": 0}
        request_id_bytes = request_id.encode("latin-1", errors="ignore")

        async def _send_wrapper(message: Message) -> None:
            if message.get("type") == "http.response.start":
                status_holder["code"] = int(message.get("status") or 0)
                headers = list(message.get("headers") or [])
                if not any(name == b"x-request-id" for name, _ in headers):
                    headers.append((b"x-request-id", request_id_bytes))
                message = {**message, "headers": headers}
            await send(message)

        started_at = time.monotonic()
        try:
            await self._app(scope, receive, _send_wrapper)
        except Exception:
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
            if not skip_log:
                _log.exception(
                    "request.exception method=%s path=%s elapsed_ms=%s",
                    method,
                    path,
                    elapsed_ms,
                    extra={
                        "http_method": method,
                        "http_path": path,
                        "elapsed_ms": elapsed_ms,
                        "client_addr": client_addr,
                        "outcome": "exception",
                    },
                )
            raise
        finally:
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
            status = status_holder["code"]
            if not skip_log and status:
                if status >= 500:
                    level = logging.ERROR
                elif status >= 400:
                    level = logging.WARNING
                elif self._should_demote_to_debug(path):
                    level = logging.DEBUG
                else:
                    level = logging.INFO
                _log.log(
                    level,
                    "request.end method=%s path=%s status=%s elapsed_ms=%s",
                    method,
                    path,
                    status,
                    elapsed_ms,
                    extra={
                        "event": "request.end",
                        "http_method": method,
                        "http_path": path,
                        "http_status": status,
                        "elapsed_ms": elapsed_ms,
                        "client_addr": client_addr,
                        "outcome": "ok" if status < 400 else ("client_error" if status < 500 else "server_error"),
                    },
                )
                if (
                    status < 500
                    and not self._should_demote_to_debug(path)
                    and self._slow_threshold_ms > 0
                    and elapsed_ms >= self._slow_threshold_ms
                ):
                    _log.warning(
                        "request.slow method=%s path=%s status=%s elapsed_ms=%s threshold_ms=%s",
                        method,
                        path,
                        status,
                        elapsed_ms,
                        self._slow_threshold_ms,
                        extra={
                            "event": "request.slow",
                            "hint": (
                                "Request exceeded SLOW_REQUEST_MS_THRESHOLD. Inspect downstream calls "
                                "(DB query, redis publish, external HTTP) for the same request_id."
                            ),
                            "http_method": method,
                            "http_path": path,
                            "http_status": status,
                            "elapsed_ms": elapsed_ms,
                            "threshold_ms": self._slow_threshold_ms,
                            "outcome": "slow",
                        },
                    )
            reset_log_context()
