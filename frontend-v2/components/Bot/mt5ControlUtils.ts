import {
  type MT5AccountItem,
  type MT5BotCatalogItem,
  type MT5BotTokenEntitlement,
  type MT5DeploymentItem,
  type StartMt5DeploymentResponse,
} from "@/lib/api";

const PRIVATE_GSALGO_BOT_CODE = "gsalgo_mt5_bot";
const GSALGO_DISPLAY_NAME = "Gs Algo";
const GSALGO_BOT_IDENTITIES = new Set([PRIVATE_GSALGO_BOT_CODE, "gsalgo"]);

export type TradingUnit = "price_distance" | "points";

export const TRADING_CONFIG_DEFAULTS = {
  lotSize: "0.00",
  stopLoss: "",
  takeProfit: "",
  tradingUnit: "price_distance" as TradingUnit,
};

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

function isPrivateGsalgoBot(
  bot: MT5BotCatalogItem | null,
  fallbackName?: string | null
): boolean {
  const values = [bot?.bot_id, bot?.bot_name, bot?.display_name, fallbackName].map((value) =>
    normalizeBotIdentity(value).replace(/[^a-z0-9_]/g, "")
  );

  return values.some((value) => GSALGO_BOT_IDENTITIES.has(value));
}

export function botSupportsTradingConfig(
  bot: MT5BotCatalogItem | null,
  fallbackName?: string | null
): boolean {
  if (isPrivateGsalgoBot(bot, fallbackName)) {
    return true;
  }
  const required = new Set(
    (bot?.required_params || []).map((item) => normalizeBotIdentity(item))
  );
  return ["lot_size", "stop_loss", "take_profit"].every((key) => required.has(key));
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
    return "không rõ hạn";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "không rõ hạn";
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

export function humanizeAccountStatus(account: MT5AccountItem | null): string {
  if (!account) {
    return "Chưa có account";
  }
  const status = String(account.status || "").trim().toLowerCase();
  if (status === "connected") return "Đã kết nối";
  if (status === "pending_verification" && account.has_credentials) return "Đã lưu account";
  if (status === "pending_verification") return "Thiếu thông tin";
  if (status === "verification_failed") return "Đăng nhập lỗi";
  return account.status || "Không rõ";
}

export function humanizeDeploymentStatus(
  deployment: MT5DeploymentItem | null,
  account: MT5AccountItem | null
): string {
  if (!deployment && !account?.active_deployment_id) {
    return "Đang tắt";
  }
  const status = String(deployment?.status || account?.active_deployment_status || "")
    .trim()
    .toLowerCase();
  if (status === "running" || status === "start_requested" || status === "starting")
    return "Đang bật";
  if (status === "stop_requested" || status === "stopped") return "Đang tắt";
  if (status === "failed" || status === "blocked") return "Lỗi";
  return status ? status : "Đang tắt";
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
