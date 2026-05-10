"""Catalog rieng cho moi error code do control plane raise.

Muc dich:
- chuan hoa response cho FE: moi error tra ve dict {public_code, message_vi, message_en, action, retryable, group}
- giam roi cho user khi gap loi (thay vi nhin code thuan tuy nhu "account_has_active_deployment")
- tach map nay khoi route handler de de mo rong (vd. them error moi chi can them entry)

Quy uoc:
- `public_code` la key user-facing, on dinh, FE co the dung de i18n hoac switch UX
- `message_vi` / `message_en` la default message; FE van duoc quyen override
- `action`: hint UX FE nen lam gi:
    - retry            : re-call API
    - stop_current     : huong dan user dung deployment cu
    - cancel_pending   : co the cancel job/command kep o queue
    - wait_maintenance : doi runner xong bao tri
    - reconnect_account: re-enter credential
    - contact_support  : khong tu fix duoc
    - upgrade_plan     : qua quota
- `retryable`: backend goi y co the retry tu dong (idempotent) khong
- `group`: gom logic cho metric/alert (account / deployment / runner / verification / runtime / system)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class ErrorEntry:
    """1 entry trong catalog."""

    public_code: str
    http_status: int
    message_vi: str
    message_en: str
    action: str
    retryable: bool
    group: str
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self, raw_detail: str | None = None) -> dict[str, Any]:
        """Build dict de gan vao HTTPException.detail."""
        payload: dict[str, Any] = {
            "error": raw_detail or self.public_code,
            "public_code": self.public_code,
            "message_vi": self.message_vi,
            "message_en": self.message_en,
            "action": self.action,
            "retryable": self.retryable,
            "group": self.group,
        }
        return payload


# Toan bo catalog. Giu order theo group de de doc/extend.
_ENTRIES: tuple[ErrorEntry, ...] = (
    # ---------------------------------------------------------------
    # ACCOUNT
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="account_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy tài khoản giao dịch này.",
        message_en="Trading account not found.",
        action="reconnect_account",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="account_not_connected",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản chưa sẵn sàng. Vui lòng kết nối lại account trước khi bật bot.",
        message_en="Account is not ready. Please reconnect the account before starting a bot.",
        action="reconnect_account",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="rate_limited",
        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        message_vi="Bạn thao tác quá nhanh. Vui lòng đợi vài giây rồi thử lại.",
        message_en="Too many requests. Please wait a few seconds and try again.",
        action="retry",
        retryable=True,
        group="system",
    ),
    ErrorEntry(
        public_code="quota_exceeded",
        http_status=status.HTTP_403_FORBIDDEN,
        message_vi="Đã đạt giới hạn bot của gói cước. Hãy nâng cấp gói hoặc dừng một bot đang chạy trước khi bật thêm.",
        message_en="You have reached the bot limit of your current plan. Upgrade your plan or stop a running bot to start a new one.",
        action="upgrade_plan",
        retryable=False,
        group="billing",
    ),
    ErrorEntry(
        public_code="account_quota_exceeded",
        http_status=status.HTTP_403_FORBIDDEN,
        message_vi="Đã đạt giới hạn số tài khoản broker của gói cước. Hãy nâng cấp gói hoặc xoá tài khoản cũ.",
        message_en="You have reached the broker account limit of your plan. Upgrade or remove an old account.",
        action="upgrade_plan",
        retryable=False,
        group="billing",
    ),
    ErrorEntry(
        public_code="invalid_risk_policy",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Cấu hình risk policy không hợp lệ. Vui lòng kiểm tra lại giá trị số/phần trăm.",
        message_en="Risk policy configuration is invalid. Please check the numeric values and percent fields.",
        action="fix_input",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="cannot_update_credentials_while_active",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Không thể đổi mật khẩu broker khi có bot đang chạy. Hãy dừng bot trước.",
        message_en="Cannot update broker password while a bot is active. Stop the bot first.",
        action="stop_current_deployment",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="invalid_credentials_payload",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Mật khẩu không hợp lệ. Vui lòng nhập mật khẩu hợp lệ (8-256 ký tự).",
        message_en="Password is invalid. Please enter a valid password (8-256 characters).",
        action="fix_input",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="account_credentials_unavailable",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Không lấy được thông tin đăng nhập broker. Vui lòng kết nối lại tài khoản.",
        message_en="Broker credentials are unavailable. Please reconnect the account.",
        action="reconnect_account",
        retryable=False,
        group="account",
    ),
    ErrorEntry(
        public_code="mt5_account_already_added",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản MT5 này đã được thêm vào danh sách của bạn.",
        message_en="This MT5 account has already been added to your account list.",
        action="reload",
        retryable=False,
        group="account",
        aliases=("account_already_added",),
    ),
    ErrorEntry(
        public_code="mt5_account_already_used",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản MT5 này đã được sử dụng.",
        message_en="This MT5 account is already in use.",
        action="contact_support",
        retryable=False,
        group="account",
        aliases=("duplicate_mt5_account", "account_identity_conflict"),
    ),
    # ---------------------------------------------------------------
    # BOT
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="bot_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Bot này không tồn tại trong catalog.",
        message_en="Bot not found in catalog.",
        action="contact_support",
        retryable=False,
        group="bot",
    ),
    ErrorEntry(
        public_code="bot_disabled",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Bot này tạm thời bị vô hiệu hoá bởi quản trị viên.",
        message_en="This bot is temporarily disabled by the administrator.",
        action="contact_support",
        retryable=False,
        group="bot",
    ),
    ErrorEntry(
        public_code="bot_reserved_for_backend_ctrader",
        http_status=status.HTTP_403_FORBIDDEN,
        message_vi="Bot này được giữ riêng cho lane cTrader, không dùng qua control plane MT5.",
        message_en="This bot is reserved for the cTrader backend lane and cannot be used here.",
        action="contact_support",
        retryable=False,
        group="bot",
    ),
    ErrorEntry(
        public_code="bot_not_available_on_runner",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Bot này chưa có runner sẵn sàng. Vui lòng thử lại sau ít phút.",
        message_en="This bot has no ready runner right now. Please try again in a few minutes.",
        action="retry",
        retryable=True,
        group="bot",
    ),
    # ---------------------------------------------------------------
    # DEPLOYMENT
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="account_has_active_deployment",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản đang có một bot đang chạy. Hãy dừng bot hiện tại trước khi bật bot mới.",
        message_en="This account already has an active bot running. Stop the current bot before starting a new one.",
        action="stop_current_deployment",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="account_runtime_orphan_requires_operator_cleanup",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản còn runtime bot cũ trên runner. Vui lòng chờ hệ thống đồng bộ hoặc liên hệ hỗ trợ.",
        message_en="This account still has an old bot runtime on the runner. Wait for sync or contact support.",
        action="contact_support",
        retryable=True,
        group="deployment",
    ),
    ErrorEntry(
        public_code="account_runtime_duplicate_requires_operator_cleanup",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản đang có dấu hiệu runtime trùng lặp. Hệ thống đã chặn lệnh bật mới để bảo vệ tài khoản.",
        message_en="This account appears to have duplicate runtimes. The system blocked the new start to protect the account.",
        action="contact_support",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="telegram_user_has_active_bot",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Telegram ID này đang có một bot đang chạy. Mỗi Telegram ID chỉ được dùng một bot tại một thời điểm.",
        message_en="This Telegram ID already has one active bot. Each Telegram ID can use only one bot at a time.",
        action="stop_current_deployment",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="bot_control_cooldown_active",
        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        message_vi="Bạn vừa bật/tắt bot. Vui lòng chờ đủ 60 giây rồi thao tác lại.",
        message_en="You recently started or stopped a bot. Please wait 60 seconds before trying again.",
        action="retry",
        retryable=True,
        group="deployment",
    ),
    ErrorEntry(
        public_code="invalid_deployment_config",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Cấu hình bot không hợp lệ. Lot size, stop loss, take profit phải lớn hơn 0 và dca_enabled phải là boolean nếu được gửi.",
        message_en="Bot configuration is invalid. Lot size, stop loss, and take profit must be greater than 0; dca_enabled must be boolean when provided.",
        action="fix_input",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="deployment_config_locked_while_active",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Không thể cập nhật cấu hình khi bot đang chạy. Hãy dừng bot rồi bật lại với cấu hình mới.",
        message_en="Cannot update deployment configuration while the bot is active. Stop it and start again with the new config.",
        action="stop_current_deployment",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="start_transition_in_progress",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Bot vừa dừng xong và hệ thống đang đồng bộ trạng thái. Vui lòng thử lại sau vài giây.",
        message_en="The bot has just stopped and state is still syncing. Please retry in a few seconds.",
        action="retry",
        retryable=True,
        group="deployment",
        aliases=("account_has_pending_command",),
    ),
    ErrorEntry(
        public_code="deployment_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy deployment này.",
        message_en="Deployment not found.",
        action="reload",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="deployment_not_running",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Deployment không ở trạng thái cho phép thao tác này.",
        message_en="Deployment is not in a state that allows this action.",
        action="reload",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="paper_mode_unavailable",
        http_status=status.HTTP_403_FORBIDDEN,
        message_vi="Bot này không hỗ trợ chế độ paper/demo. Vui lòng chọn mode 'live' hoặc chọn bot khác.",
        message_en="This bot does not support paper/demo mode. Use 'live' mode or choose a different bot.",
        action="contact_support",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="deployment_cannot_be_cancelled",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Bot đã chạy/đã dừng, không thể huỷ. Hãy dùng thao tác dừng bot bình thường nếu cần.",
        message_en="Bot has already started or finished, cannot be cancelled. Use the regular stop action instead.",
        action="stop_current_deployment",
        retryable=False,
        group="deployment",
    ),
    ErrorEntry(
        public_code="unsupported_runtime_command",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Loại lệnh này không được hỗ trợ với deployment hiện tại.",
        message_en="This runtime command type is not supported for the current deployment.",
        action="contact_support",
        retryable=False,
        group="deployment",
    ),
    # ---------------------------------------------------------------
    # VERIFICATION
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="verification_job_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy yêu cầu xác thực tài khoản này.",
        message_en="Verification job not found.",
        action="reload",
        retryable=False,
        group="verification",
    ),
    ErrorEntry(
        public_code="verification_already_pending",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản đang có một yêu cầu xác thực chờ xử lý. Vui lòng đợi hoặc huỷ yêu cầu cũ trước khi tạo mới.",
        message_en="An active verification request is already pending. Wait or cancel it before requesting a new one.",
        action="cancel_pending",
        retryable=False,
        group="verification",
    ),
    ErrorEntry(
        public_code="verification_already_completed",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Yêu cầu xác thực này đã kết thúc, không thể huỷ.",
        message_en="This verification request has already finished and cannot be cancelled.",
        action="reload",
        retryable=False,
        group="verification",
    ),
    ErrorEntry(
        public_code="verification_result_runner_mismatch",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Phản hồi xác thực không khớp runner. Yêu cầu đang được kiểm tra lại.",
        message_en="Verification result does not match the assigned runner. The request will be re-checked.",
        action="retry",
        retryable=True,
        group="verification",
        aliases=(
            "verification_result_slot_mismatch",
            "verification_result_trace_mismatch",
        ),
    ),
    # ---------------------------------------------------------------
    # SCHEDULER / RUNNER / SLOT
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="runner_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy runner.",
        message_en="Runner not found.",
        action="contact_support",
        retryable=False,
        group="runner",
    ),
    ErrorEntry(
        public_code="slot_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy slot.",
        message_en="Slot not found.",
        action="contact_support",
        retryable=False,
        group="runner",
    ),
    ErrorEntry(
        public_code="command_not_found",
        http_status=status.HTTP_404_NOT_FOUND,
        message_vi="Không tìm thấy lệnh execution.",
        message_en="Execution command not found.",
        action="reload",
        retryable=False,
        group="runner",
    ),
    ErrorEntry(
        public_code="no_scheduler_candidate",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
        message_en="The server is currently busy with too many users. Please try again later.",
        action="retry",
        retryable=True,
        group="runner",
    ),
    ErrorEntry(
        public_code="no_available_unreserved_slot",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
        message_en="The server is currently busy with too many users. Please try again later.",
        action="retry",
        retryable=True,
        group="runner",
    ),
    ErrorEntry(
        public_code="login_busy",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Tài khoản MT5 này đang chạy trên một runner khác. Hãy dừng instance hiện tại trước khi bật lại.",
        message_en="This MT5 login is already active on another runner. Stop the current instance before starting again.",
        action="stop_current",
        retryable=False,
        group="runner",
    ),
    ErrorEntry(
        public_code="login_lease_unavailable",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Hệ thống tạm thời không thể kiểm tra phiên đăng nhập, vui lòng thử lại sau ít phút.",
        message_en="The login lease service is temporarily unavailable. Please try again shortly.",
        action="retry",
        retryable=True,
        group="runner",
    ),
    ErrorEntry(
        public_code="runner_full",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
        message_en="The server is currently busy with too many users. Please try again later.",
        action="retry",
        retryable=True,
        group="runner",
        aliases=("no_healthy_slot_available", "no_available_healthy_slot"),
    ),
    ErrorEntry(
        public_code="slot_not_ipc_ready",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.",
        message_en="The MT5 runner slot is not ready yet. Please try again in a few minutes.",
        action="retry",
        retryable=True,
        group="runner",
        aliases=(
            "slot_bridge_only_not_python_ipc_ready",
            "slot_bridge_not_ready",
            "slot_resident_worker_missing",
        ),
    ),
    ErrorEntry(
        public_code="runtime_death_confirmation_required",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Runtime có dấu hiệu chết, cần xác nhận thủ công trước khi dọn dẹp.",
        message_en="Runtime appears dead and requires manual confirmation before cleanup.",
        action="contact_support",
        retryable=False,
        group="runner",
    ),
    # ---------------------------------------------------------------
    # MAINTENANCE / RUNTIME UNHEALTHY (giu pattern public_code goc)
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="mt5_runtime_maintenance",
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        message_vi="MT5 runner đang bảo trì, vui lòng thử lại sau.",
        message_en="MT5 runner is under maintenance. Please try again later.",
        action="wait_maintenance",
        retryable=True,
        group="runtime",
        aliases=(
            "runner_temporarily_maintenance",
            "runner_maintenance",
            "windows_runtime_unhealthy",
            "runner_queue_backlog",
            "runner_offline",
        ),
    ),
    # ---------------------------------------------------------------
    # SYSTEM
    # ---------------------------------------------------------------
    ErrorEntry(
        public_code="TERMS_NOT_ACCEPTED",
        http_status=status.HTTP_403_FORBIDDEN,
        message_vi="Vui lòng đọc và xác nhận Điều khoản sử dụng & Cảnh báo rủi ro trước khi tiếp tục.",
        message_en="Please read and accept the Terms of Use & Risk Warning before continuing.",
        action="accept_terms",
        retryable=False,
        group="terms",
        aliases=("terms_not_accepted",),
    ),
    ErrorEntry(
        public_code="invalid_terms_version",
        http_status=status.HTTP_409_CONFLICT,
        message_vi="Phiên bản điều khoản không còn hợp lệ. Vui lòng tải lại Mini App và xác nhận lại.",
        message_en="Terms version is no longer valid. Please reload the Mini App and accept again.",
        action="reload",
        retryable=False,
        group="terms",
    ),
    ErrorEntry(
        public_code="terms_checkboxes_required",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Vui lòng xác nhận đầy đủ các nội dung bắt buộc trước khi tiếp tục.",
        message_en="Please confirm all required acknowledgements before continuing.",
        action="fix_input",
        retryable=False,
        group="terms",
    ),
    ErrorEntry(
        public_code="invalid_request",
        http_status=status.HTTP_400_BAD_REQUEST,
        message_vi="Yêu cầu không hợp lệ.",
        message_en="Invalid request.",
        action="contact_support",
        retryable=False,
        group="system",
    ),
)


def _build_index() -> dict[str, ErrorEntry]:
    """Index code + alias -> entry de O(1) lookup."""
    index: dict[str, ErrorEntry] = {}
    for entry in _ENTRIES:
        index[entry.public_code] = entry
        for alias in entry.aliases:
            index[alias] = entry
    return index


_INDEX: dict[str, ErrorEntry] = _build_index()


def lookup(detail: str | None) -> ErrorEntry | None:
    """Tra entry theo code/alias. Tra None neu khong khop."""
    if not detail:
        return None
    return _INDEX.get(str(detail).strip())


class ControlPlaneHTTPException(HTTPException):
    """HTTPException co them `error_info` dict.

    Custom exception handler trong app.main se serialize response thanh:
        { "detail": "<raw_code>", "error_info": { ...catalog payload... } }

    Nho do FE legacy van doc duoc `detail` la string, FE moi dung `error_info`.
    """

    def __init__(
        self,
        *,
        status_code: int,
        public_code: str,
        error_info: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=public_code, headers=headers)
        self.public_code = public_code
        self.error_info = dict(error_info)


def to_http_exception(
    detail: str | None,
    *,
    fallback_status: int = status.HTTP_400_BAD_REQUEST,
    fallback_action: str = "contact_support",
    fallback_group: str = "system",
) -> ControlPlaneHTTPException:
    """Chuyen 1 raw detail string -> ControlPlaneHTTPException.

    Neu code khong co trong catalog -> tra fallback voi public_code = raw detail.
    Response body se la {"detail": "<raw_code>", "error_info": {...}} nho exception handler.
    """
    raw = (detail or "").strip()
    entry = lookup(raw)
    if entry is not None:
        return ControlPlaneHTTPException(
            status_code=entry.http_status,
            public_code=raw or entry.public_code,
            error_info=entry.to_payload(raw_detail=raw),
        )
    fallback_payload = {
        "error": raw or "unknown_error",
        "public_code": raw or "unknown_error",
        "message_vi": "Đã xảy ra lỗi không xác định. Vui lòng thử lại hoặc liên hệ hỗ trợ.",
        "message_en": "An unknown error occurred. Please retry or contact support.",
        "action": fallback_action,
        "retryable": False,
        "group": fallback_group,
    }
    return ControlPlaneHTTPException(
        status_code=fallback_status,
        public_code=raw or "unknown_error",
        error_info=fallback_payload,
    )


async def control_plane_http_exception_handler(
    request: Request,  # noqa: ARG001 - FastAPI handler signature
    exc: ControlPlaneHTTPException,
) -> JSONResponse:
    """Format response body co ca `detail` (string) + `error_info` (dict).

    Phai duoc dang ky qua `app.add_exception_handler(ControlPlaneHTTPException, handler)`
    trong app factory.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.public_code,
            "error": exc.error_info.get("error") or exc.public_code,
            "message": exc.error_info.get("message_vi") or exc.error_info.get("message_en") or exc.public_code,
            "error_info": exc.error_info,
        },
        headers=exc.headers,
    )


def all_entries() -> Mapping[str, ErrorEntry]:
    """Snapshot doc cho test/debug. Read-only."""
    return dict(_INDEX)
