import {
  type MT5AccountItem,
  type MT5BotCatalogItem,
  type MT5DeploymentItem,
} from "@/lib/api";
import { getBackendErrorCode } from "@/lib/api";
import {
  isMt5AccountReady,
  isTransitionalDeploymentStatus,
} from "@/components/Bot/mt5ControlUtils";

export type Mt5BotAction = "load" | "refresh" | "start" | "stop" | "delete" | "config";

const TRADE_DISABLED_MESSAGE =
  "Tài khoản đã đăng nhập nhưng chưa thể giao dịch. Vui lòng nạp tiền hoặc kiểm tra quyền giao dịch trên sàn.";

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Chúng tôi chưa thể xử lý yêu cầu. Vui lòng thử lại sau.";
}

function normalizeErrorCode(error: unknown): string {
  const raw = (getBackendErrorCode(error) ?? getErrorMessage(error)).trim();
  return raw.replace(/^"+|"+$/g, "").toLowerCase();
}

function isTradeDisabledReason(value: string): boolean {
  const code = value.replace(/-/g, "_");
  const readable = code.replace(/_/g, " ");
  return (
    code.includes("fatal_trading_disabled_on_server") ||
    code.includes("trading_disabled_on_server") ||
    readable.includes("trading has been disabled") ||
    readable.includes("disabled on server")
  );
}

export function getFriendlyRuntimeReason(
  action: "start" | "stop",
  rawReason?: string | null
): string | null {
  const code = String(rawReason || "").trim().replace(/^"+|"+$/g, "").toLowerCase();

  if (!code) {
    return null;
  }

  if (isTradeDisabledReason(code)) {
    return TRADE_DISABLED_MESSAGE;
  }

  if (
    code.includes("failed to fetch") ||
    code.includes("networkerror") ||
    code.includes("network request failed") ||
    code.includes("timeout")
  ) {
    return "Kết nối chưa ổn định. Vui lòng kiểm tra mạng và thử lại sau vài phút.";
  }

  if (
    code.includes("invalid slot transition") ||
    code.includes("worker_missing") ||
    code.includes("worker missing")
  ) {
    return "Bot vừa khởi động nhưng phiên làm việc chưa ổn định. Vui lòng thử lại sau vài phút.";
  }

  switch (code) {
    case "account_not_connected":
      return "Tài khoản này chưa sẵn sàng. Vui lòng kiểm tra kết nối MT5 rồi thử lại.";
    case "account_has_active_deployment":
      return "Tài khoản đang có bot chạy. Hãy tắt bot hiện tại trước.";
    case "telegram_user_has_active_bot":
      return "Bạn đang có một bot hoạt động. Hãy tắt bot đó trước khi bật bot khác.";
    case "bot_control_cooldown_active":
      return "Bạn vừa bật hoặc tắt bot. Vui lòng đợi khoảng 60 giây rồi thử lại.";
    case "account_credentials_unavailable":
      return "Thiếu thông tin đăng nhập an toàn. Vui lòng kết nối lại tài khoản MT5.";
    case "command_rejected":
      return action === "start"
        ? "Chưa thể bật bot lúc này. Vui lòng thử lại sau vài phút."
        : "Chưa thể tắt bot lúc này. Vui lòng thử lại sau vài phút.";
    case "slot_broken":
    case "runner_not_found":
    case "slot_not_found":
      return "Dịch vụ đang bận hoặc gặp sự cố tạm thời. Vui lòng thử lại sau vài phút.";
    case "mt5_runtime_maintenance":
    case "windows_runtime_unhealthy":
    case "replacement_start_failed":
    case "runner_offline":
    case "runner_queue_backlog":
      return "Phiên bot đang được khởi động lại. Vui lòng thử lại sau vài phút.";
    case "slot_not_ipc_ready":
    case "slot_resident_worker_missing":
      return "Phiên bot đang được khởi động lại. Vui lòng thử lại sau vài phút.";
    case "orphaned_handoff":
      return "Chúng tôi đang hoàn tất thao tác trước đó. Vui lòng đợi thêm vài phút rồi thử lại.";
    case "runtime_death_confirmation_required":
      return "Chúng tôi đang xác minh trạng thái an toàn trước khi tiếp tục. Vui lòng thử lại sau vài phút.";
    case "runner_full":
    case "no_available_unreserved_slot":
    case "no_scheduler_candidate":
    case "no_healthy_slot_available":
    case "no_available_healthy_slot":
      return "Lượng truy cập đang cao. Vui lòng thử lại sau vài phút.";
    default:
      break;
  }

  return null;
}

export function getFriendlyMt5ActionError(action: Mt5BotAction, error: unknown): string {
  const code = normalizeErrorCode(error);

  if (isTradeDisabledReason(code)) {
    return TRADE_DISABLED_MESSAGE;
  }

  if (
    code.includes("failed to fetch") ||
    code.includes("networkerror") ||
    code.includes("network request failed") ||
    code.includes("load failed") ||
    code.includes("timeout")
  ) {
    return "Kết nối tới máy chủ chưa ổn định. Vui lòng kiểm tra mạng và thử lại sau vài phút.";
  }

  switch (code) {
    case "telegram_init_data_missing":
      return "Vui lòng mở ứng dụng trong Telegram để tiếp tục.";
    case "rate_limited":
      return "Bạn thao tác hơi nhanh. Vui lòng chờ vài giây rồi làm mới.";
    case "account_not_found":
      return "Không còn thấy tài khoản này. Hãy làm mới danh sách rồi thử lại.";
    case "account_not_connected":
      return action === "start"
        ? "Tài khoản chưa sẵn sàng nên chưa thể bật bot."
        : "Tài khoản chưa sẵn sàng cho thao tác này.";
    case "account_has_active_deployment":
      return action === "delete"
        ? "Tài khoản đang có bot chạy. Hãy tắt bot trước khi gỡ tài khoản."
        : "Tài khoản đang có bot chạy. Hãy tắt bot hiện tại trước khi bật bot khác.";
    case "telegram_user_has_active_bot":
      return "Mỗi tài khoản Telegram chỉ chạy một bot tại một thời điểm. Hãy tắt bot hiện tại trước khi bật bot khác.";
    case "bot_control_cooldown_active":
      return "Bạn vừa bật hoặc tắt bot. Vui lòng đợi khoảng 60 giây rồi thử lại.";
    case "start_transition_in_progress":
      return action === "delete"
        ? "Thao tác bật/tắt bot trước đó chưa xong. Vui lòng chờ vài giây rồi thử gỡ tài khoản lại."
        : "Thao tác bật/tắt bot trước đó chưa xong. Vui lòng làm mới rồi thử lại.";
    case "bot_not_found":
      return "Bot bạn chọn hiện không còn khả dụng. Hãy làm mới danh sách rồi chọn lại.";
    case "bot_token_required":
      return "Vui lòng nhập mã kích hoạt để dùng bot này.";
    case "bot_token_not_found":
      return "Mã không đúng hoặc không tồn tại.";
    case "bot_token_already_used":
      return "Mã này đã được sử dụng.";
    case "bot_token_expired":
    case "bot_token_entitlement_expired":
      return "Mã hoặc quyền dùng bot đã hết hạn. Vui lòng nhập mã mới.";
    case "bot_token_revoked":
      return "Mã này đã bị vô hiệu.";
    case "bot_token_wrong_bot":
      return "Mã này không áp dụng cho bot đã chọn.";
    case "bot_token_partner_locked":
    case "bot_token_partner_expired":
      return "Đối tác cấp mã đang tạm khóa. Vui lòng liên hệ hỗ trợ.";
    case "bot_token_entitlement_not_found":
    case "bot_token_entitlement_inactive":
      return "Bạn chưa kích hoạt quyền cho bot này.";
    case "deployment_not_found":
      return "Không còn thấy phiên bot này. Hãy làm mới rồi thử lại.";
    case "deployment_config_locked_while_active":
      return "Khi bot đang chạy, vui lòng tắt bot trước khi đổi cài đặt.";
    case "invalid_deployment_config":
      return "Cài đặt bot không hợp lệ. Hãy làm mới rồi thử lại.";
    case "deployment_not_running":
      return "Bot không ở trạng thái có thể tắt. Hãy làm mới rồi thử lại.";
    case "runner_not_found":
    case "slot_not_found":
      return "Dịch vụ đang bận hoặc tạm chưa sẵn sàng. Vui lòng thử lại sau vài phút.";
    case "mt5_runtime_maintenance":
    case "windows_runtime_unhealthy":
    case "runner_offline":
    case "runner_queue_backlog":
      return "Phiên bot đang được khởi động lại. Vui lòng thử lại sau vài phút.";
    case "slot_not_ipc_ready":
    case "slot_resident_worker_missing":
      return "Phiên bot đang được khởi động lại. Vui lòng thử lại sau vài phút.";
    case "runner_full":
    case "no_available_unreserved_slot":
    case "no_scheduler_candidate":
    case "no_healthy_slot_available":
    case "no_available_healthy_slot":
      return "Lượng truy cập đang cao. Vui lòng thử lại sau vài phút.";
    case "account_credentials_unavailable":
      return "Thiếu thông tin đăng nhập an toàn. Vui lòng kết nối lại tài khoản MT5.";
    case "command_rejected":
      return action === "start"
        ? "Chưa thể bật bot lúc này. Vui lòng thử lại sau vài phút."
        : "Chưa thể tắt bot lúc này. Vui lòng thử lại sau vài phút.";
    case "slot_broken":
    case "orphaned_handoff":
      return "Chúng tôi đang đồng bộ lại trạng thái. Vui lòng thử lại sau vài phút.";
    case "runtime_death_confirmation_required":
      return "Chúng tôi đang xác minh trạng thái trước khi tiếp tục. Vui lòng thử lại sau vài phút.";
    default:
      break;
  }

  if (action === "load" || action === "refresh") {
    return "Chưa thể tải dữ liệu. Vui lòng làm mới sau vài phút.";
  }
  if (action === "start") {
    return "Chưa thể bật bot lúc này. Vui lòng thử lại sau vài phút.";
  }
  if (action === "stop") {
    return "Chưa thể tắt bot lúc này. Vui lòng thử lại sau vài phút.";
  }
  if (action === "delete") {
    return "Chưa thể gỡ tài khoản lúc này. Vui lòng làm mới rồi thử lại.";
  }
  if (action === "config") {
    return "Chưa thể lưu cài đặt lúc này. Vui lòng thử lại sau vài phút.";
  }
  return "Chúng tôi chưa thể xử lý yêu cầu. Vui lòng thử lại sau.";
}

export function getDeploymentFailureMessage(
  action: "start" | "stop",
  account: MT5AccountItem | null,
  deployment: MT5DeploymentItem | null
): string | null {
  const deploymentStatus = String(deployment?.status || "").trim().toLowerCase();
  const healthStatus = String(deployment?.health_status || "").trim().toLowerCase();
  const friendlyReason = getFriendlyRuntimeReason(
    action,
    deployment?.last_error || account?.last_error
  );

  if (action === "start") {
    if (deploymentStatus === "failed" || deploymentStatus === "blocked") {
      return friendlyReason ?? "Bot chưa khởi động ổn định. Vui lòng thử lại sau vài phút.";
    }
    if (deploymentStatus === "stopped" && deployment) {
      return (
        friendlyReason ??
        "Bot vừa dừng ngay sau khi bật. Vui lòng thử lại sau vài phút."
      );
    }
    if (healthStatus === "rejected" || healthStatus === "broken") {
      return (
        friendlyReason ??
        "Bot chưa thể bật ổn định lúc này. Vui lòng thử lại sau vài phút."
      );
    }
  }

  if (action === "stop") {
    if (healthStatus === "broken") {
      return (
        friendlyReason ??
        "Bot gặp sự cố tạm thời; chúng tôi đang xử lý. Vui lòng làm mới sau vài phút."
      );
    }
  }

  return null;
}

export function getActionHint(params: {
  selectedAccount: MT5AccountItem | null;
  selectedBot: MT5BotCatalogItem | null;
  selectedDeployment: MT5DeploymentItem | null;
  controlsLocked: boolean;
  refreshingState: boolean;
  startingBot: boolean;
  stoppingBot: boolean;
}): string | null {
  const {
    selectedAccount,
    selectedBot,
    selectedDeployment,
    controlsLocked,
    refreshingState,
    startingBot,
    stoppingBot,
  } = params;

  if (startingBot) {
    return null;
  }
  if (stoppingBot) {
    return null;
  }
  if (refreshingState) {
    return "Đang cập nhật…";
  }
  if (!selectedAccount) {
    return "Chọn tài khoản MT5";
  }
  if (!isMt5AccountReady(selectedAccount)) {
    return "Hoàn tất kết nối tài khoản";
  }
  if (!selectedBot) {
    return "Chọn bot";
  }

  const deploymentStatus = String(
    selectedDeployment?.status || selectedAccount.active_deployment_status || ""
  )
    .trim()
    .toLowerCase();
  if (
    selectedAccount.active_deployment_id &&
    isTransitionalDeploymentStatus(deploymentStatus)
  ) {
    return null;
  }
  if (selectedAccount.active_deployment_id) {
    return null;
  }
  if (controlsLocked) {
    return null;
  }

  return null;
}
