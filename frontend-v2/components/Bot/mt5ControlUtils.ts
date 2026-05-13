import {
  type MT5AccountItem,
  type MT5BotCatalogItem,
  type MT5BotTokenEntitlement,
  type MT5DeploymentItem,
  type StartMt5DeploymentResponse,
} from "@/lib/api";

const GSALGO_DISPLAY_NAME = "Gs Algo";
export const LOT_SIZE_DEFAULT = "0.01";

export function isMt5AccountReady(account: MT5AccountItem | null): boolean {
  if (!account) {
    return false;
  }
  const status = String(account.status || "").trim().toLowerCase();
  return (
    status === "connected" ||
    Boolean(account.verified_at) ||
    (status === "pending_verification" && Boolean(account.has_credentials))
  );
}

export function isTransitionalDeploymentStatus(status?: string | null): boolean {
  const normalized = String(status || "").trim().toLowerCase();
  return (
    normalized === "start_requested" ||
    normalized === "starting" ||
    normalized === "stop_requested"
  );
}

export function isActiveDeploymentStatus(status?: string | null): boolean {
  const normalized = String(status || "").trim().toLowerCase();
  return (
    normalized === "start_requested" ||
    normalized === "starting" ||
    normalized === "running" ||
    normalized === "stop_requested"
  );
}

function normalizeBotIdentity(value?: string | null): string {
  return String(value || "").trim().toLowerCase();
}

export function formatBotDisplayName(value?: string | null): string {
  const raw = String(value || "").trim();
  const normalized = normalizeBotIdentity(raw).replace(/[^a-z0-9]/g, "");
  if (normalized === "gsalgo" || normalized === "gsalgomt5bot") {
    return GSALGO_DISPLAY_NAME;
  }
  return raw;
}

export function entitlementMatchesBot(
  entitlement: MT5BotTokenEntitlement,
  bot: MT5BotCatalogItem | null
): boolean {
  if (!bot) {
    return false;
  }
  const tokenBot = normalizeBotIdentity(entitlement.bot_code);
  if (tokenBot === "*" || tokenBot === "miniapp_full_access") {
    return true;
  }
  const botValues = [bot.bot_id, bot.bot_name, bot.display_name].map(normalizeBotIdentity);
  return Boolean(tokenBot && botValues.includes(tokenBot));
}

export function formatTokenExpiry(value?: string | null): string {
  if (!value) {
    return "Chưa có thông tin hạn";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Chưa có thông tin hạn";
  }
  return date.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function parsePositiveDecimalInput(value: string): number | null {
  const raw = value.replace(/\s/g, "").trim();
  let normalized = raw;
  const lastComma = raw.lastIndexOf(",");
  const lastDot = raw.lastIndexOf(".");
  if (lastComma >= 0 && lastDot >= 0) {
    normalized =
      lastComma > lastDot ? raw.replace(/\./g, "").replace(",", ".") : raw.replace(/,/g, "");
  } else if (lastComma >= 0) {
    normalized = raw.replace(",", ".");
  }
  if (!normalized) {
    return null;
  }

  const parsed = Number(normalized);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }

  return parsed;
}

function getRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function getDeploymentLotSize(config?: Record<string, unknown> | null): string | null {
  const payload = getRecord(config);
  const trading = getRecord(payload.trading);
  const raw = trading.lot_size ?? payload.lot_size;
  if (raw == null || raw === "") {
    return null;
  }
  const parsed = Number(String(raw).replace(",", "."));
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }
  return String(raw);
}

export function humanizeAccountStatus(account: MT5AccountItem | null): string {
  if (!account) {
    return "Chưa chọn tài khoản";
  }
  const status = String(account.status || "").trim().toLowerCase();
  if (status === "connected") return "Đã kết nối";
  if (status === "pending_verification" && account.has_credentials) {
    return "Đã lưu, đang chờ xác minh";
  }
  if (status === "pending_verification") return "Cần nhập thông tin đăng nhập";
  if (status === "verification_failed") return "Không đăng nhập được";
  return account.status || "Đang cập nhật";
}

export function humanizeDeploymentStatus(
  deployment: MT5DeploymentItem | null,
  account: MT5AccountItem | null
): string {
  if (!deployment && !account?.active_deployment_id) {
    return "Đã tắt";
  }
  const status = String(deployment?.status || account?.active_deployment_status || "")
    .trim()
    .toLowerCase();
  if (status === "running" || status === "start_requested" || status === "starting") {
    return "Đang bật";
  }
  if (status === "stop_requested") return "Đang tắt";
  if (status === "stopped") return "Đã tắt";
  if (status === "failed" || status === "blocked") return "Cần thử lại";
  if (status === "queued") return "Đang chờ";
  return status ? "Đang xử lý" : "Đã tắt";
}

/**
 * Map backend (status + health_status) into short, user-facing Vietnamese.
 * Stages align with real runner events; return null when no in-flight detail applies.
 */
export function humanizeDeploymentProgress(
  deployment: MT5DeploymentItem | null
): string | null {
  if (!deployment) return null;
  const status = String(deployment.status || "").trim().toLowerCase();
  const health = String(deployment.health_status || "").trim().toLowerCase();

  if (status === "queued") {
    if (health.startsWith("waiting_previous_deployment_stop") || health === "waiting_previous_runtime_stop") {
      return "Đang chờ bot hiện tại tắt xong...";
    }
    return "Đang xếp hàng, sẽ tới lượt bạn trong giây lát...";
  }

  if (status === "start_requested") {
    if (health === "starting") return "Đang bật bot, vui lòng đợi thêm chút...";
    return "Đang bắt đầu, vui lòng đợi thêm chút...";
  }

  if (status === "starting") {
    if (health === "executor_preparing") return "Đang mở terminal và kết nối sàn...";
    if (health === "executor_ready") return "Bot đã sẵn sàng, đang chờ tín hiệu...";
    if (health === "starting") return "Đang bật bot, vui lòng đợi thêm chút...";
    return "Đang khởi động bot...";
  }

  if (status === "stop_requested") {
    if (health === "executor_stopping") return "Đang đóng lệnh và tắt bot...";
    if (health === "config_update_restart_requested") return "Đang tắt để cập nhật cài đặt...";
    if (health === "replacement_stop_requested") return "Đang chuyển sang phiên mới...";
    if (health === "stop_requested") return "Đang tắt bot, vui lòng đợi thêm chút...";
    return "Đang tắt bot...";
  }

  if (status === "running") {
    if (health === "running") return "Bot đang chạy";
    if (health === "degraded") return "Bot đang chạy — kết nối chưa ổn định";
    if (health === "executor_ready") return "Sẵn sàng nhận tín hiệu";
    return "Bot đang chạy";
  }

  return null;
}

export function getAccountStatusPillClassName(account: MT5AccountItem | null): string {
  const status = String(account?.status || "").trim().toLowerCase();

  if (
    status === "connected" ||
    Boolean(account?.verified_at) ||
    (status === "pending_verification" && Boolean(account?.has_credentials))
  ) {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "pending_verification") {
    return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  }
  if (status === "verification_failed") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-white/10 bg-transparent text-cyber-muted";
}

export function getDeploymentStatusPillClassName(
  deployment: MT5DeploymentItem | null,
  account: MT5AccountItem | null
): string {
  const status = String(deployment?.status || account?.active_deployment_status || "")
    .trim()
    .toLowerCase();

  if (status === "running") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (
    status === "start_requested" ||
    status === "starting" ||
    status === "stop_requested"
  ) {
    return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  }
  if (status === "failed" || status === "blocked") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-white/10 bg-transparent text-cyber-muted";
}

export function getLatestDeploymentForAccount(
  deployments: MT5DeploymentItem[],
  accountId: number | null
): MT5DeploymentItem | null {
  if (!accountId) {
    return null;
  }

  const items = deployments.filter((deployment) => deployment.account_id === accountId);
  if (!items.length) {
    return null;
  }

  return (
    [...items].sort((left, right) => {
      const leftTime = Date.parse(left.updated_at || left.created_at || "");
      const rightTime = Date.parse(right.updated_at || right.created_at || "");

      if (
        Number.isFinite(leftTime) &&
        Number.isFinite(rightTime) &&
        leftTime !== rightTime
      ) {
        return rightTime - leftTime;
      }

      return (right.id || 0) - (left.id || 0);
    })[0] ?? null
  );
}

export function getDeploymentByIdForAccount(
  deployments: MT5DeploymentItem[],
  accountId: number | null,
  deploymentId: number | null
): MT5DeploymentItem | null {
  if (!accountId || !deploymentId) {
    return null;
  }

  return (
    deployments.find(
      (deployment) =>
        deployment.account_id === accountId && deployment.id === deploymentId
    ) ?? null
  );
}

export function normalizeDeploymentId(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value || 0);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function getStartResponseDeploymentId(
  response: StartMt5DeploymentResponse
): number | null {
  const directId = normalizeDeploymentId(response.deployment_id);
  if (directId) {
    return directId;
  }
  const deployment = response.deployment;
  if (deployment && typeof deployment === "object" && "id" in deployment) {
    return normalizeDeploymentId((deployment as { id?: unknown }).id);
  }
  return null;
}
