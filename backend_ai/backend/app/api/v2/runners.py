from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error
from app.core.internal_auth import require_backend_api_key
from app.schemas.control_plane import (
    AccountVerificationResultRequest,
    CommandDeliveryUpdateRequest,
    GsAlgoBotStateRequest,
    RunnerCommandClaimRequest,
    RunnerDrainRequest,
    RunnerEventRequest,
    RunnerHeartbeatRequest,
    RunnerOrphanedHandoffRequest,
    RunnerRegisterRequest,
    RunnerResumeRequest,
)
from app.services.control_plane_service import MT5ControlPlaneService
from ops_telegram_alerts import schedule_error_alert

router = APIRouter(tags=["mt5-runners"])


_RUNNER_EVENT_TOP_LEVEL_PAYLOAD_KEYS = (
    "message",
    "log_message",
    "phase",
    "event_at",
    "timestamp",
    "callback_http_ms",
    "callback_elapsed_ms",
    "http_elapsed_ms",
    "elapsed_ms",
)


def _runner_event_payload_for_service(payload: RunnerEventRequest) -> dict:
    data = payload.model_dump(mode="json")
    event_payload = dict(data.get("payload") or {})
    for key in _RUNNER_EVENT_TOP_LEVEL_PAYLOAD_KEYS:
        value = data.pop(key, None)
        if value is not None and key not in event_payload:
            event_payload[key] = value
    created_at = data.get("created_at")
    if created_at is not None and "event_at" not in event_payload:
        event_payload["event_at"] = created_at
    if not data.get("command_id") and event_payload.get("command_id"):
        data["command_id"] = str(event_payload.get("command_id") or "").strip() or None
    if data.get("deployment_id") is None and event_payload.get("deployment_id"):
        try:
            data["deployment_id"] = int(event_payload["deployment_id"])
        except (TypeError, ValueError):
            pass
    data["payload"] = event_payload
    return data


@router.post("/runner/register")
async def register_runner(
    payload: RunnerRegisterRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.register_runner(**payload.model_dump(mode="json"))
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runner/heartbeat")
async def runner_heartbeat(
    payload: RunnerHeartbeatRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return await service.ingest_runner_heartbeat(**payload.model_dump(mode="json"))
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runner/events")
async def runner_events(
    payload: RunnerEventRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return await service.ingest_runner_event(**_runner_event_payload_for_service(payload))
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runner/bot-state/gsalgo")
async def runner_gsalgo_bot_state(
    payload: GsAlgoBotStateRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    data = payload.model_dump(mode="json")
    try:
        result = service.handle_gsalgo_bot_state(**data)
        if isinstance(result, dict) and result.get("ok") is False:
            context = dict(data.get("context") or {})
            schedule_error_alert(
                area="GsAlgo state bridge",
                summary="Backend từ chối một bản ghi state/candle từ runner.",
                severity="warning",
                account_id=context.get("account_id"),
                deployment_id=context.get("deployment_id"),
                runner_id=str(context.get("runner_id") or "") or None,
                slot_id=str(context.get("slot_id") or "") or None,
                impact="Audit, dashboard hoặc recovery có thể thiếu một phần dữ liệu.",
                action="Kiểm tra payload state từ runner và schema backend.",
                detail={"operation": data.get("operation"), "error": result.get("error")},
                alert_key=f"gsalgo_state_rejected:{data.get('operation')}:{result.get('error')}",
                cooldown_sec=300,
            )
        return result
    except Exception as exc:
        context = dict(data.get("context") or {})
        schedule_error_alert(
            area="GsAlgo state bridge",
            summary="Backend lỗi khi lưu state/candle từ runner.",
            exc=exc,
            account_id=context.get("account_id"),
            deployment_id=context.get("deployment_id"),
            runner_id=str(context.get("runner_id") or "") or None,
            slot_id=str(context.get("slot_id") or "") or None,
            impact="Bot vẫn có thể chạy nhưng dashboard/audit/recovery có thể thiếu dữ liệu.",
            action="Kiểm tra log backend quanh endpoint bot-state và dữ liệu state mới nhất.",
            detail={"operation": data.get("operation")},
            alert_key=f"gsalgo_state_exception:{data.get('operation')}:{type(exc).__name__}",
            cooldown_sec=180,
        )
        return {"ok": False, "error": "backend_state_rejected"}


@router.get("/runner/accounts/{account_id}/bundle")
async def get_runner_account_bundle(
    account_id: int,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.get_runner_account_bundle(account_id=account_id)
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/runner/deployments/{deployment_id}/package")
async def get_runner_deployment_package(
    deployment_id: int,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.get_runner_deployment_package(deployment_id=deployment_id)
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runner/account-verifications/result")
async def record_account_verification_result(
    payload: AccountVerificationResultRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = service.record_account_verification_result(**payload.model_dump(mode="json"))
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {
        "verification_job_id": result.get("id"),
        "status": result.get("status"),
        "verification_state": result.get("verification_state"),
        "verification_ui_state": result.get("verification_ui_state"),
        "error_code": result.get("error_code"),
        "retryable": result.get("retryable"),
        "failure_kind": result.get("failure_kind"),
        "failure_category": result.get("failure_category"),
        "user_message_key": result.get("user_message_key"),
        "verification_failure": result.get("verification_failure"),
        "trace_id": result.get("trace_id"),
        "runner_id": result.get("runner_id"),
        "slot_id": result.get("slot_id"),
        "account": result.get("account"),
        "job": result,
    }


@router.get("/runner/bootstrap")
async def runner_bootstrap(
    request: Request,
    runner_id: str = "",
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    request_base_url = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    return service.runner_bootstrap(runner_id=runner_id or None, request_base_url=request_base_url)


@router.post("/runner/commands/claim")
async def claim_runner_command(
    payload: RunnerCommandClaimRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    command_types = [str(getattr(item, "value", item) or "").strip().upper() for item in payload.command_types]
    deadline = time.monotonic() + max(0, int(payload.wait_timeout_sec or 0))
    while True:
        try:
            result = await service.claim_runner_command(
                runner_id=payload.runner_id,
                slot_id=payload.slot_id,
                command_types=command_types,
            )
        except Exception as exc:
            raise translate_control_plane_error(exc) from exc
        if not result.get("empty") or time.monotonic() >= deadline:
            return result
        await asyncio.sleep(min(1.0, max(0.1, deadline - time.monotonic())))


@router.get("/runner/commands/{command_id}")
async def get_execution_command(
    command_id: str,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    command = service.get_execution_command(command_id=command_id)
    if not command:
        raise HTTPException(status_code=404, detail="command_not_found")
    return command


@router.post("/runner/commands/{command_id}/delivery")
async def update_execution_command_delivery(
    command_id: str,
    payload: CommandDeliveryUpdateRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = service.update_execution_command_delivery(
            command_id=command_id,
            delivery_status=payload.delivery_status,
            error_text=payload.error_text,
            payload={
                **(payload.payload or {}),
                "runner_id": payload.runner_id,
                "slot_id": payload.slot_id,
                "delivery_status": payload.delivery_status,
            },
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"command_id": result.get("command_id"), "delivery_status": result.get("delivery_status"), "command": result}


@router.get("/runners")
async def list_runners(
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {"items": service.list_runners()}


@router.post("/runners/{runner_id}/maintenance/drain")
async def drain_runner(
    runner_id: str,
    payload: RunnerDrainRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.enter_runner_maintenance(
            runner_id=runner_id,
            reason=payload.reason,
            actor=payload.actor,
            disable_ready_slots=payload.disable_ready_slots,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runners/{runner_id}/maintenance/resume")
async def resume_runner(
    runner_id: str,
    payload: RunnerResumeRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.exit_runner_maintenance(
            runner_id=runner_id,
            reason=payload.reason,
            actor=payload.actor,
            enable_disabled_slots=payload.enable_disabled_slots,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/runners/{runner_id}/slots/{slot_id}/orphaned-handoff")
async def prepare_orphaned_handoff(
    runner_id: str,
    slot_id: str,
    payload: RunnerOrphanedHandoffRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.prepare_orphaned_slot_handoff(
            runner_id=runner_id,
            slot_id=slot_id,
            reason=payload.reason,
            actor=payload.actor,
            confirmed_runtime_dead=payload.confirmed_runtime_dead,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/runners/health/summary")
async def runtime_health_summary(
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return service.runtime_health_summary()


@router.get("/runners/health/dashboard")
async def runner_health_dashboard(
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return service.runner_health_dashboard()


@router.post("/runners/health/reconcile")
async def reconcile_runtime_health(
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return service.reconcile_runtime_health()


@router.get("/runners/{runner_id}/health")
async def get_runner_health(
    runner_id: str,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    runner = service.get_runner_health(runner_id=runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="runner_not_found")
    return runner


@router.get("/runners/{runner_id}")
async def get_runner(
    runner_id: str,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    runner = service.get_runner(runner_id=runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="runner_not_found")
    return runner
