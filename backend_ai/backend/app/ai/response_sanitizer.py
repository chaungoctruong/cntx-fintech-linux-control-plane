from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional


_INTERNAL_ROLE_VALUES = {"admin", "dev", "developer", "support", "staff", "internal", "ops"}
_CUSTOMER_ROLE_VALUES = {"", "customer", "end_user", "end-user", "user", "guest", "client"}

_EXPLICIT_DEBUG_PHRASES = (
    "show raw debug",
    "raw debug",
    "debug raw",
    "show debug",
    "hien thi log ky thuat",
    "hiển thị log kỹ thuật",
    "cho toi deployment_id",
    "cho tôi deployment_id",
    "cho toi command_id",
    "cho tôi command_id",
    "cho toi runner_id",
    "cho tôi runner_id",
    "cho toi slot_id",
    "cho tôi slot_id",
    "toi la dev",
    "tôi là dev",
    "can debug",
    "cần debug",
)

_INTERNAL_LABEL_PATTERN = re.compile(
    r"(?i)\b("
    r"deployment_id|command_id|runner_id|slot_id|node_id|account_id|"
    r"user_id|telegram_id|database_id|db_id|account_slot|trace_id|job_id"
    r")\b\s*[:=]\s*[`'\"]?[^,\s;`'\")\]}]+[`'\"]?"
)

_INTERNAL_KEYWORD_PATTERN = re.compile(
    r"(?i)\b("
    r"deployment_id|command_id|runner_id|slot_id|node_id|user_id|telegram_id|account_slot|"
    r"AIContextBuilder|intent_router|knowledge_loader|knowledge loader|"
    r"injected files|backend context|raw json context|raw context|"
    r"stack trace|traceback|pm2"
    r")\b"
)

_INTERNAL_PATH_PATTERN = re.compile(
    r"(?i)(?:/root/|backend_ai/backend|[a-z]:\\[^ \n\r\t]+|(?:^|[\s(])/[a-z0-9_\-./]+)"
)
_INTERNAL_INFRA_PATTERN = re.compile(
    r"(?i)\b(?:redis|stream:mt5|mt5:runner:[^\s]+|mt5:execution:[^\s]+|localhost|127\.0\.0\.1)\b"
)
_PRIVATE_IP_PATTERN = re.compile(r"\b(?:10|127|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s+(?:is|la|là)\s+[^\s,;]+"),
    re.compile(r"(?i)\b(api\s*key|private\s*key|bearer)\b\s+[^\s,;]+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s+(?:(?:của|cua)\s+(?:tôi|toi|em|anh|chị|chi|mình|minh)\s+)?(?:là|la)\s+[^\s,;]+"),
    re.compile(r"(?i)\b(mk|otp|2fa)\b\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:redis|postgres|postgresql|mysql|mongodb)://[^\s]+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


@dataclass(frozen=True)
class PublicAnswerProfile:
    user_role: str = "customer"
    debug_allowed: bool = False

    @property
    def is_customer(self) -> bool:
        return not self.debug_allowed


def _normalize_text(text: object) -> str:
    raw = str(text or "").strip().lower()
    raw = raw.replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "debug"}


def normalize_user_role(value: object) -> str:
    role = str(value or "").strip().lower().replace(" ", "_")
    if role in _INTERNAL_ROLE_VALUES:
        return role
    if role in _CUSTOMER_ROLE_VALUES:
        return "customer"
    return "customer"


def explicit_debug_requested(user_msg: str) -> bool:
    norm = _normalize_text(user_msg)
    if not norm:
        return False
    return any(_normalize_text(phrase) in norm for phrase in _EXPLICIT_DEBUG_PHRASES)


def build_public_answer_profile(
    *,
    user_msg: str = "",
    context: Optional[dict[str, Any]] = None,
    user_role: object = None,
    debug: object = None,
) -> PublicAnswerProfile:
    ctx = context or {}
    role = normalize_user_role(
        user_role
        if user_role is not None
        else ctx.get("user_role")
        or ctx.get("role")
        or ctx.get("actor_role")
    )
    debug_flag = _truthy(debug if debug is not None else ctx.get("debug") or ctx.get("debug_mode"))
    debug_allowed = bool(debug_flag and role in _INTERNAL_ROLE_VALUES)
    return PublicAnswerProfile(user_role=role, debug_allowed=debug_allowed)


def mask_internal_reference(value: object) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))
    if len(raw) <= 4:
        return "ẩn"
    return f"...{raw[-4:]}"


def sanitize_public_answer(text: str, profile: PublicAnswerProfile) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    if profile.debug_allowed:
        return raw

    protected_commands: list[str] = []

    def _stash_command(match: re.Match[str]) -> str:
        protected_commands.append(match.group(0))
        return f"__CMD_{len(protected_commands) - 1}__"

    raw = re.sub(r"(?<![A-Za-z0-9_])/(?:start|help|menu)\b", _stash_command, raw, flags=re.IGNORECASE)

    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        line_s = str(line or "").strip()
        if not line_s:
            cleaned_lines.append("")
            continue
        line_l = line_s.lower()
        if any(
            marker in line_l
            for marker in (
                "aicontextbuilder",
                "intent_router",
                "knowledge loader",
                "injected files",
                "backend_ai/backend",
                "stack trace",
                "traceback",
                "stream:mt5",
                "mt5:runner:",
                "mt5:execution:",
                "/root/",
                "c:\\",
                "pm2",
            )
        ):
            continue
        cleaned_lines.append(line_s)

    cleaned = "\n".join(cleaned_lines).strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted_sensitive]", cleaned)
    cleaned = _INTERNAL_LABEL_PATTERN.sub("mã phiên kiểm tra [ẩn]", cleaned)
    cleaned = _INTERNAL_KEYWORD_PATTERN.sub("mã kỹ thuật nội bộ", cleaned)
    cleaned = _INTERNAL_PATH_PATTERN.sub(" [đường dẫn nội bộ] ", cleaned)
    cleaned = _INTERNAL_INFRA_PATTERN.sub("hệ thống nội bộ", cleaned)
    cleaned = _PRIVATE_IP_PATTERN.sub("máy chủ nội bộ", cleaned)

    replacements = {
        "Backend": "Hệ thống",
        "backend": "hệ thống",
        "context backend": "dữ liệu hệ thống",
        "backend context": "dữ liệu hệ thống",
        "raw JSON": "dữ liệu kỹ thuật",
        "raw json": "dữ liệu kỹ thuật",
        "runner/slot heartbeat": "kết nối vận hành",
        "Runner/slot": "Phiên vận hành",
        "runner/slot": "phiên vận hành",
        "runner": "hệ thống vận hành",
        "Runner": "Hệ thống vận hành",
        "slot": "phiên chạy",
        "Slot": "Phiên chạy",
        "deployment": "phiên bot",
        "Deployment": "Phiên bot",
        "Command": "Lệnh xử lý",
        "command": "lệnh xử lý",
        "RUNNING": "đang chạy",
        "running": "đang chạy",
        "STOPPED": "đang dừng",
        "stopped": "đang dừng",
        "order_send": "gửi lệnh sang MT5",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)

    cleaned = re.sub(r"\{[^{}\n]*(?:mã kỹ thuật nội bộ|mã phiên kiểm tra)[^{}\n]*\}", "dữ liệu kỹ thuật đã ẩn", cleaned)
    cleaned = re.sub(r"\[[^\[\]\n]*(?:mã kỹ thuật nội bộ|mã phiên kiểm tra)[^\[\]\n]*\]", "[dữ liệu kỹ thuật đã ẩn]", cleaned)
    for idx, command in enumerate(protected_commands):
        cleaned = cleaned.replace(f"__CMD_{idx}__", command)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip(" -:\n\t")
    if not cleaned:
        return "Mình đã kiểm tra hệ thống nhưng phần kỹ thuật nội bộ đã được ẩn. Bạn gửi thêm ảnh trạng thái hoặc thời điểm lỗi để mình hỗ trợ tiếp."
    return cleaned
