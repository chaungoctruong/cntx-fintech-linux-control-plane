"""Format audit_logs row -> user-friendly activity entry.

Audit rows hien tai luu raw `action` (vd `deployment.start`) + `payload_json`.
FE Mini App hien thi tieng Viet -> can mapping action -> chuoi nguoi dung doc duoc.

Doi voi action chua biet, tra ve fallback voi action raw (de no break ban dau,
nhung user van thay duoc cai gi do).
"""
from __future__ import annotations

from typing import Any, Optional


def _account_label(payload: dict[str, Any]) -> str:
    parts = []
    if payload.get("broker"):
        parts.append(str(payload["broker"]))
    if payload.get("login"):
        parts.append(str(payload["login"]))
    if not parts and payload.get("account_id") is not None:
        parts.append(f"#{payload['account_id']}")
    return "/".join(parts) or "—"


def _deployment_label(payload: dict[str, Any]) -> str:
    bot = payload.get("bot_name") or payload.get("bot_code")
    dep_id = payload.get("deployment_id")
    if bot and dep_id is not None:
        return f"{bot} (#{dep_id})"
    if dep_id is not None:
        return f"#{dep_id}"
    if bot:
        return str(bot)
    return "—"


_ACTION_FORMATTERS: dict[str, dict[str, Any]] = {
    "account.connect": {
        "vi": lambda p: f"Kết nối tài khoản {_account_label(p)}",
        "en": lambda p: f"Connected account {_account_label(p)}",
        "severity": "info",
    },
    "account.credentials.update": {
        "vi": lambda p: f"Đổi mật khẩu broker cho tài khoản {_account_label(p)} (cần đăng nhập lại)",
        "en": lambda p: f"Rotated broker password for account {_account_label(p)} (runtime login required)",
        "severity": "warning",
    },
    "account.login_slot.requested": {
        "vi": lambda p: f"Giữ slot đăng nhập cho tài khoản #{p.get('account_id')}",
        "en": lambda p: f"Reserved login slot for account #{p.get('account_id')}",
        "severity": "info",
    },
    "account.login_slot.result": {
        "vi": lambda p: f"Đăng nhập tài khoản #{p.get('account_id')} - kết quả: {'OK' if p.get('ok') else 'thất bại'}",
        "en": lambda p: f"Runtime login for account #{p.get('account_id')} - result: {'OK' if p.get('ok') else 'failed'}",
        "severity": "info",
    },
    "bot.select": {
        "vi": lambda p: f"Chọn bot {p.get('bot_name')} cho tài khoản #{p.get('account_id')}",
        "en": lambda p: f"Selected bot {p.get('bot_name')} for account #{p.get('account_id')}",
        "severity": "info",
    },
    "deployment.start": {
        "vi": lambda p: f"Bật bot {p.get('bot_name')} cho tài khoản #{p.get('account_id')}",
        "en": lambda p: f"Started bot {p.get('bot_name')} for account #{p.get('account_id')}",
        "severity": "info",
    },
    "deployment.stop": {
        "vi": lambda p: f"Dừng bot deployment #{p.get('deployment_id')}",
        "en": lambda p: f"Stopped bot deployment #{p.get('deployment_id')}",
        "severity": "info",
    },
    "deployment.cancel": {
        "vi": lambda p: (
            f"Hủy bot deployment #{p.get('deployment_id')} đang chờ khởi động "
            f"(trạng thái cũ: {p.get('previous_status')})"
        ),
        "en": lambda p: (
            f"Cancelled pending deployment #{p.get('deployment_id')} "
            f"(was {p.get('previous_status')})"
        ),
        "severity": "info",
    },
    "deployment.config.update": {
        "vi": lambda p: f"Cập nhật cấu hình bot {_deployment_label(p)}",
        "en": lambda p: f"Updated bot configuration for {_deployment_label(p)}",
        "severity": "info",
    },
    "account.risk_policy.update": {
        "vi": lambda p: f"Cập nhật risk policy cho tài khoản #{p.get('account_id')}",
        "en": lambda p: f"Updated risk policy for account #{p.get('account_id')}",
        "severity": "info",
    },
    "account.circuit_breaker.trigger": {
        "vi": lambda p: (
            f"Tự động dừng bot tài khoản #{p.get('account_id')} do vượt daily loss "
            f"(PnL today: {p.get('realized_pnl_today')})"
        ),
        "en": lambda p: (
            f"Auto-stopped bots on account #{p.get('account_id')} due to daily loss breach "
            f"(PnL today: {p.get('realized_pnl_today')})"
        ),
        "severity": "warning",
    },
    "user.delete": {
        "vi": lambda p: "Yêu cầu xoá tài khoản người dùng (GDPR)",
        "en": lambda p: "User account deletion request (GDPR)",
        "severity": "warning",
    },
}


def format_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert 1 audit_logs row -> activity entry user-friendly."""
    action = str(row.get("action") or "").strip()
    payload = row.get("payload_json")
    if not isinstance(payload, dict):
        try:
            import json as _json

            payload = _json.loads(payload) if isinstance(payload, str) else {}
        except Exception:
            payload = {}
    payload = dict(payload or {})

    formatter = _ACTION_FORMATTERS.get(action)
    if formatter is None:
        summary_vi = f"Hành động hệ thống: {action}"
        summary_en = f"System action: {action}"
        severity = "info"
    else:
        try:
            summary_vi = formatter["vi"](payload)
        except Exception:
            summary_vi = action
        try:
            summary_en = formatter["en"](payload)
        except Exception:
            summary_en = action
        severity = str(formatter.get("severity") or "info")

    related: dict[str, Any] = {}
    for key in ("account_id", "deployment_id", "login_reservation_id", "bot_name"):
        if key in payload and payload[key] is not None:
            related[key] = payload[key]

    return {
        "id": row.get("id"),
        "action": action,
        "summary_vi": summary_vi,
        "summary_en": summary_en,
        "severity": severity,
        "result": str(row.get("result") or ""),
        "created_at": int(row.get("created_at") or 0),
        "trace_id": row.get("trace_id"),
        "related": related,
    }


def format_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [format_audit_row(r) for r in (rows or [])]
