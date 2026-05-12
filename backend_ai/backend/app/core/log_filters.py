from __future__ import annotations

import logging
from typing import Any

from app.settings import settings


def access_log_noise_filter_enabled() -> bool:
    return bool(getattr(settings, "ACCESS_LOG_NOISE_FILTER_ENABLED", True))


class ControlPlaneAccessLogNoiseFilter(logging.Filter):
    """Suppress low-value uvicorn access logs while keeping real API failures visible."""

    _STATIC_PREFIXES = (
        "/_next/static/",
        "/_next/image",
    )
    _STATIC_EXACT_PATHS = {
        "/favicon.ico",
        "/cntx-labs-logo.svg",
    }
    _LOCAL_CLIENTS = {
        "127.0.0.1",
        "::1",
        "localhost",
        "none",
    }
    _NOISY_SUCCESS_ENDPOINTS = {
        ("GET", "/ready"),
        ("GET", "/health"),
        ("GET", "/api/v2/bots"),
        ("GET", "/api/v2/mini/bots"),
        ("GET", "/api/v2/miniapp/access"),
        ("GET", "/api/v2/miniapp/accounts"),
        ("GET", "/api/v2/miniapp/dashboard"),
        ("GET", "/api/v2/miniapp/deployments"),
        ("GET", "/api/v2/miniapp/terms/status"),
        ("GET", "/api/v2/miniapp/bot-token/entitlements"),
        ("GET", "/api/v2/wallet/info"),
        ("GET", "/api/v2/wallet/transactions"),
        ("GET", "/api/v2/system/ops-summary"),
        ("GET", "/api/v2/system/runner-readiness"),
        ("POST", "/api/v2/runner/heartbeat"),
        ("POST", "/api/v2/runner/events"),
        ("POST", "/api/v2/runner/account-verifications/result"),
    }

    @staticmethod
    def _parse_access_record(record: logging.LogRecord) -> tuple[str, str, str, int] | None:
        args: Any = getattr(record, "args", ())
        if not isinstance(args, tuple) or len(args) < 5:
            return None
        try:
            client_addr = str(args[0] or "").strip()
            method = str(args[1] or "").strip().upper()
            full_path = str(args[2] or "").strip()
            status_code = int(args[4])
        except Exception:
            return None
        return client_addr, method, full_path, status_code

    @staticmethod
    def _path_parts(full_path: str) -> tuple[str, str]:
        path, _, query = str(full_path or "").partition("?")
        return path or "/", query

    @classmethod
    def _is_noisy_success_endpoint(cls, method: str, path: str) -> bool:
        if (method, path) in cls._NOISY_SUCCESS_ENDPOINTS:
            return True

        if method == "GET" and path.startswith("/api/v2/accounts/verifications/"):
            return True

        if method == "POST" and path.startswith("/api/v2/runner/commands/") and path.endswith("/delivery"):
            return True

        return False

    def filter(self, record: logging.LogRecord) -> bool:
        if not access_log_noise_filter_enabled():
            return True

        parsed = self._parse_access_record(record)
        if parsed is None:
            return True

        client_addr, method, full_path, status_code = parsed
        if status_code >= 400:
            return True

        path, query = self._path_parts(full_path)
        normalized_client = client_addr.split(":", 1)[0].strip().lower()

        if path.startswith(self._STATIC_PREFIXES) or path in self._STATIC_EXACT_PATHS:
            return False

        if "_rsc=" in query:
            return False

        if self._is_noisy_success_endpoint(method, path):
            return False

        if (
            method == "GET"
            and path == "/api/v2/public/cntx-labs/overview"
            and normalized_client in self._LOCAL_CLIENTS
        ):
            return False

        return True


def install_control_plane_access_log_filter() -> None:
    if not access_log_noise_filter_enabled():
        return

    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(existing, ControlPlaneAccessLogNoiseFilter) for existing in logger.filters):
        return
    logger.addFilter(ControlPlaneAccessLogNoiseFilter())
