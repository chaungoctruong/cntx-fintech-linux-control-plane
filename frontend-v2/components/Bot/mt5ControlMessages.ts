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

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Hiện chưa thể xử lý yêu cầu bot.";
}

function normalizeErrorCode(error: unknown): string {
  const raw = (getBackendErrorCode(error) ?? getErrorMessage(error)).trim();
  return raw.replace(/^"+|"+$/g, "").toLowerCase();
}

export function getFriendlyRuntimeReason(
  action: "start" | "stop",
  rawReason?: string | null
): string | null {
  const code = String(rawReason || "").trim().replace(/^"+|"+$/g, "").toLowerCase();

  if (!code) {
    return null;
  }

  if (
    code.includes("failed to fetch") ||
    code.includes("networkerror") ||
    code.includes("network request failed") ||
    code.includes("timeout")
  ) {
    return "Không thể kết nối ổn định tới hạ tầng bot lúc này. Vui lòng thử lại sau ít phút.";
  }

  if (
    code.includes("invalid slot transition") ||
    code.includes("worker_missing") ||
    code.includes("worker missing")
  ) {
    return "Máy chạy MT5 vừa khởi động bot nhưng slot chưa giữ được phiên chạy. Vui lòng thử lại sau ít phút.";
  }

  switch (code) {
    case "account_not_connected":
      return "Account này chưa sẵn sàng nên chưa thể xử lý thao tác bot.";
    case "account_has_active_deployment":
      return "Tài khoản này đang có bot hoạt động. Hãy tắt bot hiện tại trước.";
    case "telegram_user_has_active_bot":
      return "Telegram ID này đang có bot hoạt động. Hãy tắt bot hiện tại trước khi bật bot khác.";
    case "bot_control_cooldown_active":
      return "Bạn vừa bật/tắt bot. Vui lòng chờ đủ 60 giây rồi thao tác lại.";
    case "account_credentials_unavailable":
      return "Phiên kết nối đang thiếu thông tin an toàn. Hãy kết nối lại account.";
    case "command_rejected":
      return action === "start"
        ? "Hệ thống chưa chấp nhận lệnh bật bot ở thời điểm này. Vui lòng thử lại sau ít phút."
        : "Hệ thống chưa chấp nhận lệnh tắt bot ở thời điểm này. Vui lòng thử lại sau ít phút.";
    case "slot_broken":
    case "runner_not_found":
    case "slot_not_found":
      return "Hạ tầng chạy bot đang tạm bận hoặc gặp sự cố. Vui lòng thử lại sau ít phút.";
    case "mt5_runtime_maintenance":
    case "windows_runtime_unhealthy":
    case "replacement_start_failed":
    case "runner_offline":
    case "runner_queue_backlog":
      return "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.";
    case "slot_not_ipc_ready":
    case "slot_resident_worker_missing":
      return "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.";
    case "orphaned_handoff":
      return "Hệ thống đang khôi phục phiên chạy trước đó. Vui lòng đợi thêm ít phút rồi thử lại.";
    case "runtime_death_confirmation_required":
      return "Hệ thống đang kiểm tra lại trạng thái runtime để đảm bảo an toàn. Vui lòng thử lại sau ít phút.";
    case "runner_full":
    case "no_available_unreserved_slot":
    case "no_scheduler_candidate":
    case "no_healthy_slot_available":
    case "no_available_healthy_slot":
      return "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.";
    default:
      break;
  }

  return null;
}

export function getFriendlyMt5ActionError(action: Mt5BotAction, error: unknown): string {
  const code = normalizeErrorCode(error);

  if (
    code.includes("failed to fetch") ||
    code.includes("networkerror") ||
    code.includes("network request failed") ||
    code.includes("load failed") ||
    code.includes("timeout")
  ) {
    return "Không thể kết nối tới máy chủ lúc này. Vui lòng kiểm tra mạng và thử lại sau ít phút.";
  }

  switch (code) {
    case "telegram_init_data_missing":
      return "Vui lòng mở trong Telegram Mini App để tiếp tục.";
    case "rate_limited":
      return "Bạn thao tác hơi nhanh. Vui lòng chờ vài giây rồi làm mới trạng thái.";
    case "account_not_found":
      return "Không tìm thấy account này nữa. Hãy làm mới danh sách rồi thử lại.";
    case "account_not_connected":
      return action === "start"
        ? "Account chưa sẵn sàng nên chưa thể bật bot."
        : "Account này hiện chưa sẵn sàng cho thao tác đó.";
    case "account_has_active_deployment":
      return action === "delete"
        ? "Tài khoản này đang có bot hoạt động. Hãy tắt bot trước khi xóa account."
        : "Tài khoản này đang có bot chạy rồi. Hãy tắt bot hiện tại trước khi bật bot mới.";
    case "telegram_user_has_active_bot":
      return "Mỗi Telegram ID chỉ được dùng 1 bot tại một thời điểm. Hãy tắt bot hiện tại trước khi bật bot khác.";
    case "bot_control_cooldown_active":
      return "Bạn vừa bật/tắt bot. Vui lòng chờ đủ 60 giây rồi thao tác lại.";
    case "start_transition_in_progress":
      return action === "delete"
        ? "Account đang có thao tác bật/tắt bot chưa đồng bộ xong. Vui lòng chờ vài giây rồi xóa lại."
        : "Account đang có thao tác bật/tắt bot chưa đồng bộ xong. Vui lòng làm mới rồi thử lại.";
    case "bot_not_found":
      return "Bot bạn chọn hiện không còn khả dụng. Hãy làm mới danh sách bot rồi chọn lại.";
    case "bot_token_required":
      return "Bạn cần nhập token để mở quyền cho bot này.";
    case "bot_token_not_found":
      return "Token không đúng hoặc không tồn tại.";
    case "bot_token_already_used":
      return "Token này đã được sử dụng.";
    case "bot_token_expired":
    case "bot_token_entitlement_expired":
      return "Token hoặc quyền dùng bot đã hết hạn. Vui lòng nhập token mới.";
    case "bot_token_revoked":
      return "Token này đã bị khóa.";
    case "bot_token_wrong_bot":
      return "Token này không dùng cho bot đã chọn.";
    case "bot_token_partner_locked":
    case "bot_token_partner_expired":
      return "Đối tác cấp token đang bị khóa. Vui lòng liên hệ hỗ trợ.";
    case "bot_token_entitlement_not_found":
    case "bot_token_entitlement_inactive":
      return "Bạn chưa mở quyền token cho bot này.";
    case "deployment_not_found":
      return "Không tìm thấy phiên chạy bot này nữa. Hãy làm mới trạng thái rồi thử lại.";
    case "deployment_config_locked_while_active":
      return "Bot đang chạy chưa hỗ trợ đổi cấu hình. Hãy tắt bot rồi thử lại.";
    case "invalid_deployment_config":
      return "Cấu hình bot không hợp lệ. Hãy làm mới trạng thái rồi thử lại.";
    case "deployment_not_running":
      return "Bot hiện không ở trạng thái có thể tắt. Hãy làm mới trạng thái rồi thử lại.";
    case "runner_not_found":
    case "slot_not_found":
      return "Hạ tầng bot đang bận hoặc runner tạm thời chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    case "mt5_runtime_maintenance":
    case "windows_runtime_unhealthy":
    case "runner_offline":
    case "runner_queue_backlog":
      return "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.";
    case "slot_not_ipc_ready":
    case "slot_resident_worker_missing":
      return "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.";
    case "runner_full":
    case "no_available_unreserved_slot":
    case "no_scheduler_candidate":
    case "no_healthy_slot_available":
    case "no_available_healthy_slot":
      return "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.";
    case "account_credentials_unavailable":
      return "Phiên kết nối đang thiếu thông tin đăng nhập an toàn. Hãy kết nối lại account.";
    case "command_rejected":
      return action === "start"
        ? "Hệ thống chưa nhận lệnh bật bot ở thời điểm này. Vui lòng thử lại sau ít phút."
        : "Hệ thống chưa nhận lệnh tắt bot ở thời điểm này. Vui lòng thử lại sau ít phút.";
    case "slot_broken":
    case "orphaned_handoff":
      return "Hạ tầng chạy bot đang tạm thời cần đồng bộ lại. Vui lòng thử lại sau ít phút.";
    case "runtime_death_confirmation_required":
      return "Hệ thống đang kiểm tra trạng thái runtime trước khi xử lý tiếp. Vui lòng thử lại sau ít phút.";
    default:
      break;
  }

  if (action === "load" || action === "refresh") {
    return "Chưa thể tải trạng thái bot lúc này. Vui lòng thử làm mới lại sau ít phút.";
  }
  if (action === "start") {
    return "Chưa thể bật bot lúc này. Vui lòng thử lại sau ít phút.";
  }
  if (action === "stop") {
    return "Chưa thể tắt bot lúc này. Vui lòng thử lại sau ít phút.";
  }
  if (action === "delete") {
    return "Chưa thể xóa account lúc này. Vui lòng làm mới rồi thử lại.";
  }
  if (action === "config") {
    return "Chưa thể cập nhật cấu hình bot lúc này. Vui lòng thử lại sau ít phút.";
  }
  return "Hệ thống chưa thể xử lý yêu cầu này lúc này.";
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
      return (
        friendlyReason ?? "Bot chưa thể khởi động ổn định. Vui lòng thử lại sau ít phút."
      );
    }
    if (deploymentStatus === "stopped" && deployment) {
      return (
        friendlyReason ??
        "Bot vừa bật nhưng runtime dừng ngay sau đó. Vui lòng thử lại sau ít phút."
      );
    }
    if (healthStatus === "rejected" || healthStatus === "broken") {
      return (
        friendlyReason ??
        "Hạ tầng chạy bot đang từ chối hoặc chưa giữ được phiên khởi động. Vui lòng thử lại sau ít phút."
      );
    }
  }

  if (action === "stop") {
    if (healthStatus === "broken") {
      return (
        friendlyReason ??
        "Bot đang ở trạng thái lỗi và hệ thống đang cố gắng đồng bộ lại. Bạn có thể làm mới sau ít phút."
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
    return "Đang làm mới";
  }
  if (!selectedAccount) {
    return "Chưa có account";
  }
  if (!isMt5AccountReady(selectedAccount)) {
    return "Chưa sẵn sàng";
  }
  if (!selectedBot) {
    return "Chưa có bot";
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
