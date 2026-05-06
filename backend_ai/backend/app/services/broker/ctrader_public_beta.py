from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .ctrader_api_client import CTraderBrokerApiClient


def _normalize_error_detail(detail: str) -> str:
    normalized = (detail or "").strip()
    if not normalized:
        return "temporary_unavailable"
    if normalized in {"ctrader_backend_timeout", "ctrader_service_timeout"}:
        return "ctrader_service_timeout"
    if normalized in {
        "ctrader_backend_url_required",
        "ctrader_backend_unreachable",
        "ctrader_service_unavailable",
    }:
        return "ctrader_service_unavailable"
    if normalized.startswith("ctrader_backend_http_"):
        prefix, _, downstream_detail = normalized.partition(":")
        return _normalize_error_detail(downstream_detail or prefix)
    if normalized.startswith("ctrader_backend_"):
        return "ctrader_service_unavailable"
    return normalized


def _capabilities() -> list[str]:
    return [
        "connect_account",
        "sync_accounts",
        "save_default_account",
        "start_beta_deployment",
        "manual_evaluate",
        "monitor_status",
        "stop_beta_deployment",
    ]


def _manual_beta_description(status: str) -> str:
    if status == "offline":
        return "Dịch vụ cTrader đang tạm gián đoạn. User nên chờ trước khi kết nối hoặc bật beta."
    if status == "degraded":
        return "Public beta vẫn online nhưng có tín hiệu runtime cần theo dõi. Nên dùng demo và kiểm tra monitor trước."
    return "Public beta đang cho phép kết nối, chọn account, arm deployment, đánh giá thủ công và theo dõi runtime. Vòng tự trade liên tục vẫn chưa mở."


def _availability_label(status: str) -> str:
    if status == "offline":
        return "Tạm gián đoạn"
    if status == "degraded":
        return "Cần theo dõi"
    return "Public beta online"


async def build_ctrader_public_beta_overview(
    *,
    client: Optional[CTraderBrokerApiClient] = None,
) -> dict[str, Any]:
    if client is None:
        try:
            client = CTraderBrokerApiClient()
        except Exception as exc:
            error_detail = _normalize_error_detail(str(exc) or exc.__class__.__name__)
            return _fallback_payload(error_detail=error_detail)

    results = await asyncio.gather(
        client.list_bots(),
        client.get_runtime_session_pool_status(),
        client.get_runtime_deployment_reconciler_status(),
        return_exceptions=True,
    )
    bots_result, session_pool_result, reconciler_result = results

    blockers: list[str] = []
    visible_bots = 0
    execution_ready = False
    bot_error: str | None = None
    if isinstance(bots_result, Exception):
        bot_error = _normalize_error_detail(str(bots_result) or bots_result.__class__.__name__)
    elif isinstance(bots_result, dict):
        raw_items = bots_result.get("items")
        if isinstance(raw_items, list):
            visible_bots = len(
                [item for item in raw_items if isinstance(item, dict) and not bool(item.get("is_template"))]
            )
        raw_blockers = bots_result.get("blockers")
        if isinstance(raw_blockers, list):
            blockers = [str(item).strip() for item in raw_blockers if str(item).strip()]
        execution_ready = bool(bots_result.get("execution_ready"))

    session_pool_error: str | None = None
    session_pool_payload: dict[str, Any] = {}
    if isinstance(session_pool_result, Exception):
        session_pool_error = _normalize_error_detail(str(session_pool_result) or session_pool_result.__class__.__name__)
    elif isinstance(session_pool_result, dict):
        session_pool_payload = session_pool_result

    reconciler_error: str | None = None
    reconciler_payload: dict[str, Any] = {}
    if isinstance(reconciler_result, Exception):
        reconciler_error = _normalize_error_detail(str(reconciler_result) or reconciler_result.__class__.__name__)
    elif isinstance(reconciler_result, dict):
        reconciler_payload = reconciler_result

    availability_status = _derive_availability_status(
        session_pool_status=str(session_pool_payload.get("status") or "").strip().lower(),
        reconciler_health=str(reconciler_payload.get("health_status") or "").strip().lower(),
        visible_bots=visible_bots,
        runtime_errors=[session_pool_error, reconciler_error, bot_error],
    )

    limitations = list(blockers)
    if not execution_ready and "bot_execution_orchestrator_not_implemented" not in limitations:
        limitations.append("bot_execution_orchestrator_not_implemented")

    return {
        "provider": "ctrader",
        "surface": "public_beta",
        "availability_status": availability_status,
        "availability_label_vi": _availability_label(availability_status),
        "description_vi": _manual_beta_description(availability_status),
        "execution_mode": "manual_beta_only",
        "execution_mode_label_vi": "Public beta thủ công",
        "capabilities": _capabilities(),
        "visible_bots": visible_bots,
        "execution_ready": execution_ready,
        "blockers": limitations,
        "updated_at": int(time.time()),
        "session_pool": {
            "status": str(session_pool_payload.get("status") or "offline"),
            "running": bool(session_pool_payload.get("running")),
            "account_sessions": session_pool_payload.get("account_sessions")
            if isinstance(session_pool_payload.get("account_sessions"), dict)
            else {},
            "error": session_pool_error,
        },
        "deployment_reconciler": {
            "health_status": str(reconciler_payload.get("health_status") or "offline"),
            "running": bool(reconciler_payload.get("running")),
            "coordinator_status": str(reconciler_payload.get("coordinator_status") or "offline"),
            "last_success_at": reconciler_payload.get("last_success_at"),
            "last_failure_at": reconciler_payload.get("last_failure_at"),
            "last_error": reconciler_payload.get("last_error") or reconciler_error,
            "last_result": reconciler_payload.get("last_result")
            if isinstance(reconciler_payload.get("last_result"), dict)
            else {},
        },
    }


def _fallback_payload(*, error_detail: str) -> dict[str, Any]:
    return {
        "provider": "ctrader",
        "surface": "public_beta",
        "availability_status": "offline",
        "availability_label_vi": _availability_label("offline"),
        "description_vi": _manual_beta_description("offline"),
        "execution_mode": "manual_beta_only",
        "execution_mode_label_vi": "Public beta thủ công",
        "capabilities": _capabilities(),
        "visible_bots": 0,
        "execution_ready": False,
        "blockers": ["bot_execution_orchestrator_not_implemented"],
        "updated_at": int(time.time()),
        "session_pool": {
            "status": "offline",
            "running": False,
            "account_sessions": {},
            "error": error_detail,
        },
        "deployment_reconciler": {
            "health_status": "offline",
            "running": False,
            "coordinator_status": "offline",
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": error_detail,
            "last_result": {},
        },
    }


def _derive_availability_status(
    *,
    session_pool_status: str,
    reconciler_health: str,
    visible_bots: int,
    runtime_errors: list[Optional[str]],
) -> str:
    if any(error for error in runtime_errors if error):
        return "offline"
    if visible_bots <= 0:
        return "degraded"
    if session_pool_status == "ok" and reconciler_health in {"ok", "disabled"}:
        return "online"
    if session_pool_status in {"starting", "partial"} or reconciler_health in {"starting", "standby"}:
        return "degraded"
    return "degraded"
