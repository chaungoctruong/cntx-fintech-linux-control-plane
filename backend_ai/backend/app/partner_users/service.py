from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.services.control_plane_service import MT5ControlPlaneService

from . import audit, telegram_notify
from .deps import _redis_client
from .schemas import BotStatus, PartnerUserContext


log = logging.getLogger("partner-user.service")


# Redis key cho mapping JTI → account_id (managed bởi backend, không phải token-bot).
LINK_KEY_PREFIX = "partneruser:link:"
LINK_TTL_SEC = 60 * 86400  # 60 ngày — quá dài để token tồn tại + buffer


def _link_key(jti: str) -> str:
    return f"{LINK_KEY_PREFIX}{jti}"


def get_linked_account_id(jti: str) -> int | None:
    rc = _redis_client()
    if rc is None:
        return None
    try:
        raw = rc.get(_link_key(jti))
    except Exception:
        log.exception("link_get_failed jti=%s", jti)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return int(data.get("account_id"))
    except Exception:
        return None


def _get_token_state(jti: str) -> dict[str, Any] | None:
    """Đọc state mirror do token-bot ghi (bot_id, end_user_label, ...)."""
    rc = _redis_client()
    if rc is None:
        return None
    prefix = (
        getattr(__import__("app.settings", fromlist=["settings"]).settings,
                "PARTNER_USER_REDIS_KEY_PREFIX", None)
        or "tokenbot:jti:"
    )
    try:
        raw = rc.get(f"{prefix}{jti}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def transfer_link(old_jti: str, new_jti: str) -> dict[str, Any]:
    """Copy link mapping old_jti → new_jti khi token gia hạn.

    Idempotent: nếu new_jti đã có link → skip. Nếu old_jti không có link → noop.
    """
    rc = _redis_client()
    if rc is None:
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "State store offline"},
        )
    try:
        new_existing = rc.get(_link_key(new_jti))
        if new_existing:
            return {"transferred": False, "note": "new_jti_already_linked"}
        old_raw = rc.get(_link_key(old_jti))
        if not old_raw:
            return {"transferred": False, "note": "old_jti_has_no_link"}
        old_ttl = rc.ttl(_link_key(old_jti))
        ttl = old_ttl if (old_ttl and old_ttl > 0) else LINK_TTL_SEC
        rc.set(_link_key(new_jti), old_raw, ex=ttl)
    except Exception:
        log.exception("transfer_link_failed old=%s new=%s", old_jti, new_jti)
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "Transfer link thất bại"},
        )
    try:
        info = json.loads(old_raw)
    except Exception:
        info = {}
    audit.transfer_link(
        old_jti=old_jti, new_jti=new_jti, transferred=True,
        account_id=info.get("account_id"),
    )
    return {
        "transferred": True,
        "account_id": info.get("account_id"),
        "ttl_sec": ttl,
    }


def set_linked_account_id(jti: str, account_id: int) -> dict[str, Any]:
    rc = _redis_client()
    if rc is None:
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "State store offline"},
        )
    payload = {
        "account_id": int(account_id),
        "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        rc.set(_link_key(jti), json.dumps(payload), ex=LINK_TTL_SEC)
    except Exception:
        log.exception("link_set_failed jti=%s account_id=%s", jti, account_id)
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "Lưu link account thất bại"},
        )
    return payload


class PartnerUserService:
    """Wrapper trên MT5ControlPlaneService để khách của đối tác bật/tắt bot
    qua JWT thay vì Telegram initData.

    Không tự dispatch lệnh — gọi service hiện có. Khách không có user record;
    service core cần `telegram_id` của chủ account nên ta dò từ DB.
    """

    def __init__(self, service: MT5ControlPlaneService):
        self._svc = service

    # ---------- helpers ----------
    def _resolve_account(self, account_id_or_login: int) -> dict | None:
        """Tra cứu account theo DB id hoặc MT5 login (khách thường chỉ biết login).

        Trả về dict {id, user_id, login, broker, server, telegram_id} hoặc None.
        """
        repo = getattr(self._svc, "_repo", None)
        if repo is None or not hasattr(repo, "_store"):
            return None

        def _do(con, cur):
            cur.execute(
                """
                SELECT a.id, a.user_id, a.login, a.broker, a.server, u.telegram_id
                FROM broker_accounts a
                JOIN users u ON u.id = a.user_id
                WHERE a.id = %s OR a.login = %s
                LIMIT 1
                """,
                (int(account_id_or_login), str(account_id_or_login)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        try:
            return repo._store._with_retry_read(_do)
        except Exception:
            log.exception("resolve_account_failed input=%s", account_id_or_login)
            return None

    def _account_owner_telegram_id(self, account_id: int) -> str | None:
        """Backward-compat: chỉ trả telegram_id (input có thể là DB id hoặc login)."""
        info = self._resolve_account(account_id)
        return str(info["telegram_id"]) if info and info.get("telegram_id") else None

    def _active_deployment(self, account_id: int) -> dict[str, Any] | None:
        repo = getattr(self._svc, "_repo", None)
        if repo is None:
            return None
        fn = getattr(repo, "get_active_deployment_for_account", None)
        if fn is None:
            return None
        try:
            return fn(account_id=account_id)
        except Exception:
            log.exception("active_deployment_lookup_failed account_id=%s", account_id)
            return None

    def _to_status(self, ctx: PartnerUserContext, deployment: dict[str, Any] | None) -> BotStatus:
        if not deployment:
            return BotStatus(
                account_id=ctx.account_id,
                bot_id=ctx.bot_id,
                deployment_id=None,
                status="stopped",
            )
        return BotStatus(
            account_id=ctx.account_id,
            bot_id=ctx.bot_id,
            deployment_id=deployment.get("id") or deployment.get("deployment_id"),
            status=str(deployment.get("status") or "unknown"),
            runner_id=deployment.get("runner_id"),
            started_at=deployment.get("started_at"),
            last_event_at=deployment.get("last_event_at") or deployment.get("updated_at"),
        )

    def _require_linked(self, ctx: PartnerUserContext) -> int:
        if ctx.account_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "public_code": "account_not_linked_yet",
                    "message": "Bạn chưa link MT5 account. Gọi POST /partner-user/link-account trước.",
                },
            )
        return int(ctx.account_id)

    # ---------- public ops ----------
    def status(self, ctx: PartnerUserContext) -> BotStatus:
        if ctx.account_id is None:
            return BotStatus(
                account_id=0, bot_id=ctx.bot_id,
                deployment_id=None, status="unlinked",
            )
        return self._to_status(ctx, self._active_deployment(ctx.account_id))

    async def start(
        self,
        ctx: PartnerUserContext,
        *,
        lot_size: float | None,
        config_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        account_id = self._require_linked(ctx)
        deployment = self._active_deployment(account_id)
        if deployment and str(deployment.get("status") or "").lower() == "running":
            return {
                "action": "noop",
                "deployment": deployment,
                "note": "Bot đã đang chạy. Không cần start lại.",
            }

        owner_tg = self._account_owner_telegram_id(account_id)
        if not owner_tg:
            raise HTTPException(
                status_code=404,
                detail={
                    "public_code": "account_owner_missing",
                    "message": f"MT5 account {account_id} không tìm được owner. Liên hệ admin.",
                },
            )

        overrides: dict[str, Any] = dict(config_overrides or {})
        if lot_size is not None:
            overrides["lot_size"] = lot_size

        try:
            result = await self._svc.start_deployment(
                telegram_id=str(owner_tg),
                username=None,
                account_id=account_id,
                bot_name=ctx.bot_id,
                bot_config_overrides=overrides,
                mode="live",
            )
        except HTTPException:
            raise
        except Exception as e:
            log.exception(
                "partner_user_start_failed jti=%s account_id=%s bot=%s",
                ctx.jti,
                account_id,
                ctx.bot_id,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "public_code": "dispatch_failed",
                    "message": f"Backend không dispatch được lệnh: {type(e).__name__}",
                },
            )

        audit.bot_start(
            jti=ctx.jti, account_id=account_id, bot_id=ctx.bot_id,
            partner_id=ctx.partner_id, action="start",
        )
        return {"action": "start", "deployment": result.get("deployment"), "raw": result}

    def link_account(self, ctx: PartnerUserContext, account_id: int) -> dict[str, Any]:
        """Khách link MT5 account_id của họ với JTI.

        Quy tắc one-time:
        - Nếu JTI đã link account khác → 409 (denied).
        - Nếu JTI đã link cùng account → 200 idempotent (no change).
        - Chỉ link được account đã có owner trong broker_accounts (tồn tại trong hệ thống).
        """
        # Check existing link
        existing = get_linked_account_id(ctx.jti)
        if existing is not None:
            if int(existing) == int(account_id):
                return {
                    "account_id": int(existing),
                    "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "already_linked_same_account",
                }
            audit.link_denied(
                jti=ctx.jti,
                requested_account_id=int(account_id),
                existing_account_id=int(existing),
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "public_code": "already_linked_to_other",
                    "message": (
                        f"Token này đã link với MT5 account {existing}. "
                        f"Không thể đổi sang account khác. Liên hệ đối tác để cấp token mới."
                    ),
                },
            )

        # Resolve input (có thể là DB id hoặc MT5 login string-as-int) → canonical DB id
        acc = self._resolve_account(account_id)
        if not acc:
            raise HTTPException(
                status_code=404,
                detail={
                    "public_code": "account_not_found",
                    "message": (
                        f"Không tìm thấy MT5 account {account_id}. "
                        f"Kiểm tra lại số tài khoản hoặc liên hệ đối tác."
                    ),
                },
            )
        canonical_id = int(acc["id"])
        # Idempotent: re-link nếu đã link cùng canonical id
        if existing is not None and int(existing) == canonical_id:
            return {
                "account_id": canonical_id,
                "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "already_linked_same_account",
            }
        info = set_linked_account_id(ctx.jti, canonical_id)
        audit.link_account(
            jti=ctx.jti, account_id=canonical_id,
            partner_id=ctx.partner_id, end_user_label=ctx.end_user_label,
        )
        return {
            "account_id": canonical_id,
            "linked_at": info["linked_at"],
            "mt5_login": acc.get("login"),
            "broker": acc.get("broker"),
            "server": acc.get("server"),
        }

    async def _dm_end_user(self, *, jti: str, account_id: int, reason: str) -> bool:
        """DM khách qua hubot Telegram bot. Tự lookup telegram_id + bot_id từ state."""
        owner_tg = self._account_owner_telegram_id(account_id)
        if not owner_tg:
            log.warning("dm_skip jti=%s account_id=%s reason=no_owner_telegram", jti, account_id)
            return False
        state = _get_token_state(jti) or {}
        bot_id = state.get("bot_id") or "?"
        end_user_label = state.get("end_user_label")
        text = telegram_notify.build_force_stop_message(
            end_user_label=end_user_label, bot_id=str(bot_id), reason=reason,
        )
        return await telegram_notify.send_dm(int(owner_tg), text)

    async def force_stop_for_jti(self, *, jti: str, reason: str) -> dict[str, Any]:
        """Internal: token-bot gọi khi token expired/revoked → dừng bot khách + DM khách.

        Resolve JTI → account_id qua link mapping. Nếu khách chưa link account
        thì noop (không DM được — không biết chat_id).
        """
        account_id = get_linked_account_id(jti)
        if account_id is None:
            return {
                "action": "noop",
                "deployment": None,
                "note": "no_account_linked",
            }

        # DM ngay khi nhận force-stop (kể cả bot không chạy, để khách biết token chết)
        dm_sent = await self._dm_end_user(jti=jti, account_id=account_id, reason=reason)
        deployment = self._active_deployment(account_id)
        if not deployment:
            return {
                "action": "noop",
                "deployment": None,
                "note": "no_active_deployment",
                "dm_sent": dm_sent,
            }
        deployment_id = deployment.get("id") or deployment.get("deployment_id")
        owner_tg = self._account_owner_telegram_id(account_id)
        if not deployment_id or not owner_tg:
            return {
                "action": "noop",
                "deployment": deployment,
                "note": "missing_deployment_id_or_owner",
            }
        try:
            result = await self._svc.stop_deployment(
                telegram_id=str(owner_tg),
                username=None,
                deployment_id=int(deployment_id),
                reason=reason,
            )
        except Exception as e:
            log.exception(
                "force_stop_failed jti=%s account_id=%s deployment_id=%s",
                jti,
                account_id,
                deployment_id,
            )
            return {
                "action": "error",
                "deployment": deployment,
                "note": f"{type(e).__name__}: {e}",
            }
        log.info(
            "force_stop ok jti=%s account_id=%s deployment_id=%s reason=%s",
            jti,
            account_id,
            deployment_id,
            reason,
        )
        audit.force_stop(
            jti=jti, account_id=account_id, reason=reason,
            action="stop", dm_sent=dm_sent,
        )
        return {
            "action": "stop",
            "deployment": result.get("deployment"),
            "account_id": account_id,
            "dm_sent": dm_sent,
            "raw": result,
        }

    async def stop(self, ctx: PartnerUserContext, *, reason: str | None = None) -> dict[str, Any]:
        account_id = self._require_linked(ctx)
        deployment = self._active_deployment(account_id)
        if not deployment:
            return {
                "action": "noop",
                "deployment": None,
                "note": "Bot không chạy. Không cần stop.",
            }
        deployment_id = deployment.get("id") or deployment.get("deployment_id")
        if not deployment_id:
            return {
                "action": "noop",
                "deployment": deployment,
                "note": "Không xác định deployment_id.",
            }

        owner_tg = self._account_owner_telegram_id(account_id)
        if not owner_tg:
            raise HTTPException(
                status_code=404,
                detail={
                    "public_code": "account_owner_missing",
                    "message": f"MT5 account {account_id} không tìm được owner.",
                },
            )

        try:
            result = await self._svc.stop_deployment(
                telegram_id=str(owner_tg),
                username=None,
                deployment_id=int(deployment_id),
                reason=reason or f"partner_user:{ctx.partner_id}:khach={ctx.end_user_label}",
            )
        except HTTPException:
            raise
        except Exception as e:
            log.exception(
                "partner_user_stop_failed jti=%s deployment_id=%s", ctx.jti, deployment_id
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "public_code": "dispatch_failed",
                    "message": f"Backend không stop được: {type(e).__name__}",
                },
            )

        audit.bot_stop(
            jti=ctx.jti, account_id=account_id, bot_id=ctx.bot_id,
            partner_id=ctx.partner_id, action="stop",
        )
        log.info(
            "partner_user_stop ok jti=%s partner=%s deployment_id=%s khach=%r",
            ctx.jti,
            ctx.partner_id,
            deployment_id,
            ctx.end_user_label,
        )
        return {"action": "stop", "deployment": result.get("deployment"), "raw": result}
