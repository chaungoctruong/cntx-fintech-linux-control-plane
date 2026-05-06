from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from app.core.internal_auth import require_backend_api_key
from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error
from app.ai.training_data import AITrainingDataStore
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.store_service import get_process_store
from app.settings import settings
from app.store import SECURITY_CRITICAL_AUDIT_ACTIONS

router = APIRouter(prefix="/admin/maintenance", tags=["admin-maintenance"])
log = logging.getLogger("api_gateway.admin")


def store_dep() -> Any:
    return get_process_store()


class TokenExpiryStopRequest(BaseModel):
    telegram_id: str = Field(min_length=1)
    deployment_id: int = Field(gt=0)
    entitlement_id: Optional[str] = None
    reason: str = Field(default="bot_token_expired", max_length=200)
    bot_code: Optional[str] = None
    account_id: Optional[int] = Field(default=None, gt=0)


class AITrainingReviewRequest(BaseModel):
    example_ids: list[int] = Field(min_length=1, max_length=200)
    status: str = Field(pattern="^(approved|rejected)$")
    reviewer_id: str = Field(default="ops", max_length=120)
    quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    note: str = Field(default="", max_length=500)


class AIModelRegisterRequest(BaseModel):
    model_key: str = Field(min_length=1, max_length=180)
    base_model: str = Field(min_length=1, max_length=180)
    adapter_path: str = Field(default="", max_length=1000)
    dataset_export_key: str = Field(default="", max_length=180)
    status: str = Field(default="candidate", pattern="^(candidate|staging|active|retired|failed)$")
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _norm_bot_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _guard_token_expiry_stop(payload: TokenExpiryStopRequest, store: Any) -> dict[str, Any]:
    """Prevent stale token-expiry jobs from stopping a deployment covered by a newer active entitlement."""

    def _do(_con: Any, cur: Any) -> dict[str, Any]:
        cur.execute(
            """
            SELECT id, user_id, account_id, bot_code, bot_name, status, desired_state
            FROM bot_deployments
            WHERE id = %s
            LIMIT 1
            """,
            (int(payload.deployment_id),),
        )
        deployment = dict(cur.fetchone() or {})
        if not deployment:
            return {"allow_stop": True, "reason": "deployment_not_found_defer_to_control_plane"}

        deployment_account_id = int(deployment.get("account_id") or 0)
        deployment_bot_identity = _norm_bot_identity(deployment.get("bot_code") or deployment.get("bot_name"))
        payload_bot_identity = _norm_bot_identity(payload.bot_code)
        telegram_id = str(payload.telegram_id or "").strip()

        if payload.account_id is not None and int(payload.account_id) != deployment_account_id:
            return {"allow_stop": False, "reason": "token_expiry_account_mismatch"}
        if payload_bot_identity and payload_bot_identity != deployment_bot_identity:
            return {"allow_stop": False, "reason": "token_expiry_bot_mismatch"}

        entitlement = {}
        entitlement_id = str(payload.entitlement_id or "").strip()
        if entitlement_id:
            cur.execute(
                """
                SELECT *
                FROM bot_token_entitlements
                WHERE entitlement_id = %s
                LIMIT 1
                """,
                (entitlement_id,),
            )
            entitlement = dict(cur.fetchone() or {})
            if not entitlement:
                return {"allow_stop": False, "reason": "token_expiry_entitlement_not_found"}
            if str(entitlement.get("telegram_id") or "").strip() != telegram_id:
                return {"allow_stop": False, "reason": "token_expiry_entitlement_user_mismatch"}
            entitlement_account_id = entitlement.get("account_id")
            if entitlement_account_id is not None and int(entitlement_account_id) != deployment_account_id:
                return {"allow_stop": False, "reason": "token_expiry_entitlement_account_mismatch"}
            entitlement_bot_identity = _norm_bot_identity(entitlement.get("bot_code"))
            if entitlement_bot_identity and entitlement_bot_identity != deployment_bot_identity:
                return {"allow_stop": False, "reason": "token_expiry_entitlement_bot_mismatch"}
            if str(entitlement.get("status") or "").strip().lower() == "active" and entitlement.get("expires_at"):
                cur.execute("SELECT %s::timestamptz > NOW() AS entitlement_still_active", (entitlement.get("expires_at"),))
                if bool((cur.fetchone() or {}).get("entitlement_still_active")):
                    return {"allow_stop": False, "reason": "token_expiry_entitlement_still_active"}

        cur.execute(
            """
            SELECT entitlement_id, expires_at
            FROM bot_token_entitlements
            WHERE telegram_id = %s
              AND account_id = %s
              AND status = 'active'
              AND expires_at > NOW()
              AND regexp_replace(lower(COALESCE(bot_code, '')), '[^a-z0-9]+', '', 'g') = %s
            ORDER BY expires_at DESC, id DESC
            LIMIT 1
            """,
            (telegram_id, deployment_account_id, deployment_bot_identity),
        )
        active = dict(cur.fetchone() or {})
        if active:
            return {
                "allow_stop": False,
                "reason": "newer_active_entitlement_exists",
                "active_entitlement_id": active.get("entitlement_id"),
            }

        return {"allow_stop": True, "reason": "token_expiry_stop_allowed"}

    try:
        return store._with_retry_read(_do, tries=1)
    except Exception as exc:
        log.warning("token_expiry_stop_guard_failed deployment_id=%s err=%s", payload.deployment_id, str(exc)[:180])
        return {"allow_stop": True, "reason": "token_expiry_guard_unavailable"}


@router.post("/audit-cleanup")
async def admin_audit_cleanup(
    payload: Optional[dict] = Body(default=None),
    _: dict = Depends(require_backend_api_key),
    store: Any = Depends(store_dep),
) -> dict:
    body = payload if isinstance(payload, dict) else {}
    retention_count = int(
        body.get("retention_count")
        or getattr(settings, "AUDIT_PER_USER_RETENTION_COUNT", 1000)
        or 1000
    )
    retention_count = max(1, min(retention_count, 1_000_000))
    dry_run = bool(body.get("dry_run", False))
    result = store.prune_audit_logs_keep_last_n_per_user(
        retention_count,
        list(SECURITY_CRITICAL_AUDIT_ACTIONS),
        dry_run=dry_run,
    )
    return {
        "deleted_count": int(result.get("deleted_count") or 0),
        "scanned_users": int(result.get("scanned_users") or 0),
        "dry_run": bool(result.get("dry_run", dry_run)),
    }


@router.post("/token-expiry/stop-deployment")
async def admin_token_expiry_stop_deployment(
    payload: TokenExpiryStopRequest,
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
    store: Any = Depends(store_dep),
) -> dict:
    """Internal bridge: expired token -> Control Plane STOP_BOT.

    This endpoint does not talk to Windows directly. It reuses the standard
    control-plane stop flow so the command is persisted and routed to the
    selected Windows runner queue.
    """

    normalized_reason = (payload.reason or "bot_token_expired")[:200]
    guard = _guard_token_expiry_stop(payload, store)
    if not bool(guard.get("allow_stop")):
        return {
            "ok": True,
            "noop": True,
            "deployment_id": int(payload.deployment_id),
            "entitlement_id": payload.entitlement_id,
            "reason": str(guard.get("reason") or "token_expiry_stop_guarded"),
            "guard": guard,
        }
    try:
        result = await service.stop_deployment(
            telegram_id=str(payload.telegram_id),
            username=None,
            deployment_id=int(payload.deployment_id),
            reason=normalized_reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {
        "entitlement_id": payload.entitlement_id,
        "reason": normalized_reason,
        **result,
    }


@router.get("/ai-training/stats")
async def admin_ai_training_stats(
    _: dict = Depends(require_backend_api_key),
    store: Any = Depends(store_dep),
) -> dict:
    return AITrainingDataStore(store=store).stats_sync()


@router.get("/ai-training/examples")
async def admin_ai_training_examples(
    status: str = "pending",
    mode: str = "",
    limit: int = 50,
    offset: int = 0,
    _: dict = Depends(require_backend_api_key),
    store: Any = Depends(store_dep),
) -> dict:
    examples = AITrainingDataStore(store=store).list_examples_sync(
        status=status,
        mode=mode,
        limit=limit,
        offset=offset,
    )
    return {
        "examples": [example.__dict__ for example in examples],
        "status": status,
        "mode": mode or "all",
        "limit": max(1, min(int(limit or 50), 200)),
        "offset": max(0, int(offset or 0)),
    }


@router.post("/ai-training/examples/review")
async def admin_ai_training_review_examples(
    payload: AITrainingReviewRequest,
    _: dict = Depends(require_backend_api_key),
    store: Any = Depends(store_dep),
) -> dict:
    changed = AITrainingDataStore(store=store).review_examples_sync(
        example_ids=payload.example_ids,
        status=payload.status,
        reviewer_id=payload.reviewer_id,
        quality_score=payload.quality_score,
        note=payload.note,
    )
    return {"changed": changed, "status": payload.status}


@router.post("/ai-training/models/register")
async def admin_ai_register_model_version(
    payload: AIModelRegisterRequest,
    _: dict = Depends(require_backend_api_key),
    store: Any = Depends(store_dep),
) -> dict:
    AITrainingDataStore(store=store).register_model_version_sync(
        model_key=payload.model_key,
        base_model=payload.base_model,
        adapter_path=payload.adapter_path,
        dataset_export_key=payload.dataset_export_key,
        status=payload.status,
        metrics=payload.metrics,
        metadata=payload.metadata,
    )
    return {
        "registered": True,
        "model_key": payload.model_key,
        "status": payload.status,
    }
