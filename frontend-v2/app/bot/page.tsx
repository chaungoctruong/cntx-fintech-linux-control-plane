"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Bot,
  ChevronRight,
  CheckCircle2,
  KeyRound,
  Loader2,
  RefreshCcw,
} from "lucide-react";

import BottomNav from "@/components/BottomNav";
import { INTERNAL_BOT_NAV_MARKER } from "@/components/BottomNav";
import MiniappTermsModal from "@/components/Bot/MiniappTermsModal";
import Mt5BotControlPanel from "@/components/Bot/Mt5BotControlPanel";
import PageHeader from "@/components/PageHeader";
import { useMiniappTerms } from "@/hooks/useMiniappTerms";
import {
  BackendAPIError,
  connectMt5Account,
  createCTraderAuthorizeUrl,
  discoverCTraderAccounts,
  evaluateCTraderDeployment,
  fetchCTraderAccounts,
  fetchCTraderConnections,
  fetchCTraderRuntimeState,
  claimMt5BotToken,
  fetchMt5Accounts,
  fetchMt5BotCatalog,
  fetchMiniappAccess,
  getBackendErrorCode,
  pollMt5AccountVerificationJob,
  refreshCTraderConnection,
  requestMt5AccountVerification,
  selectDefaultCTraderAccount,
  startCTraderDeployment,
  stopCTraderDeployment,
  type CTraderBotCatalogItem,
  type CTraderBetaOverview,
  type CTraderBrokerConnection,
  type CTraderDeployment,
  type CTraderDeploymentEvent,
  type CTraderTradingAccount,
  type ConnectMt5AccountRequest,
  type MT5AccountItem,
  type MT5BotCatalogItem,
} from "@/lib/api";
import { readStoredMt5Broker, writeStoredMt5Broker } from "@/lib/mt5-preferences";
import { getTelegramTenantUserId, waitForTelegramTenantUserId } from "@/lib/telegram";

type BrokerLane = "mt5" | "ctrader";
type Mt5WorkspaceTab = "connect" | "control";
type CTraderEnvironmentFilter = "all" | "demo" | "live";

type BrokerPreset = {
  name: string;
  lane: BrokerLane;
};

const DBG_MARKETS_BROKER_NAME = "DBG Markets";
const DBG_MARKETS_FIXED_SERVER = "DBGMarkets-Live";
const DBG_MARKETS_SERVER_PENDING_LABEL = "Server đang cập nhật";
const SERVER_PENDING_BROKER_NAMES = ["Exness", "XM", "Vantage"] as const;

const brokerPresets: BrokerPreset[] = [
  {
    name: "Exness",
    lane: "mt5",
  },
  {
    name: "XM",
    lane: "mt5",
  },
  {
    name: "Vantage",
    lane: "mt5",
  },
  {
    name: DBG_MARKETS_BROKER_NAME,
    lane: "mt5",
  },
  {
    name: "IC Markets",
    lane: "ctrader",
  },
];

const IC_MARKETS_API_LANE_COPY = {
  title: "IC Markets API Lane",
  lead: "Hạ tầng thực thi tốc độ cao dành cho bot API, scalping và chiến lược HFT có kiểm soát.",
  paragraphs: [
    "API Lane được thiết kế như một tuyến giao dịch riêng, nơi tín hiệu, lệnh và quản trị rủi ro được xử lý qua lớp API thay vì phụ thuộc hoàn toàn vào EA hoặc terminal MT5 truyền thống. Cách tiếp cận này giúp giảm độ trễ trung gian, tăng tốc độ phản hồi và hỗ trợ các chiến lược cần xử lý tín hiệu liên tục ở cấp mili-giây.",
    "Phù hợp cho scalping, signal automation, copy execution, bot tần suất cao và các mô hình giao dịch cần tốc độ thực thi ổn định, kiểm soát lệnh chính xác và khả năng giám sát rủi ro theo thời gian thực.",
  ],
};

const inputClassName =
  "w-full rounded-2xl border border-white/10 bg-transparent px-4 py-3 text-sm text-white placeholder-cyber-muted outline-none transition focus:border-cyan-300/40 focus:bg-black/[0.06]";
const toneStyles = {
  success: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100",
  error: "border-rose-400/25 bg-rose-400/10 text-rose-100",
  info: "border-cyan-300/25 bg-cyan-300/10 text-cyan-100",
} as const;

type NoticeTone = keyof typeof toneStyles;

type Notice = {
  tone: NoticeTone;
  message: string;
};

type Mt5VerificationPhase = "SUBMITTED" | "ASSIGNED" | "VERIFYING_MT5" | "VERIFIED" | "FAILED";

type CTraderOnboardingTarget = "account_list" | "selection" | "control";

const MT5_VERIFICATION_PHASE_LABEL: Record<Mt5VerificationPhase, string> = {
  SUBMITTED: "Đang lưu thông tin...",
  ASSIGNED: "Đang chuẩn bị account...",
  VERIFYING_MT5: "Đang chuẩn bị account...",
  VERIFIED: "Đã lưu account thành công.",
  FAILED: "MT5 báo lỗi đăng nhập.",
};

type FormState = {
  broker: string;
  server: string;
  login: string;
  password: string;
  label: string;
};

function getErrorMessage(error: unknown): string {
  const backendCode = getBackendErrorCode(error);
  if (backendCode === "account_quota_exceeded") {
    return "Bạn đã đạt giới hạn số tài khoản MT5 của gói hiện tại. Hãy xóa tài khoản cũ hoặc liên hệ hỗ trợ để mở thêm slot.";
  }
  if (error instanceof Error) {
    const detail = error.message.trim();
    if (detail === "account_quota_exceeded") {
      return "Bạn đã đạt giới hạn số tài khoản MT5 của gói hiện tại. Hãy xóa tài khoản cũ hoặc liên hệ hỗ trợ để mở thêm slot.";
    }
    if (detail === "account_discovery_in_progress") {
      return "Hệ thống đang đồng bộ tài khoản cTrader. Vui lòng chờ vài giây rồi thử lại.";
    }
    if (detail === "ctrader_token_rate_limited") {
      return "cTrader đang giới hạn tần suất xác thực. Vui lòng đợi một chút rồi thử lại.";
    }
    if (detail === "ctrader_backend_timeout") {
      return "Kết nối tới cTrader đang chậm. Vui lòng thử lại sau ít phút.";
    }
    if (detail === "ctrader_backend_unreachable") {
      return "Lane cTrader tạm thời chưa phản hồi. Vui lòng thử lại sau.";
    }
    if (detail === "ctrader_service_timeout") {
      return "Dịch vụ cTrader đang phản hồi chậm. Vui lòng thử lại sau ít phút.";
    }
    if (detail === "ctrader_service_unavailable") {
      return "Dịch vụ cTrader đang tạm gián đoạn. Vui lòng thử lại sau.";
    }
    if (detail === "deployment_already_active") {
      return "Tài khoản này đã có deployment cTrader beta đang bật.";
    }
    if (detail === "deployment_not_running") {
      return "Deployment cTrader hiện không ở trạng thái đang chạy.";
    }
    if (detail === "deployment_not_found") {
      return "Deployment cTrader không còn tồn tại. Hãy làm mới trạng thái rồi thử lại.";
    }
    if (detail === "deployment_already_active_with_different_config") {
      return "Tài khoản này đã có deployment beta với cấu hình khác. Hãy dừng deployment cũ trước.";
    }
    if (detail === "contract_bot_not_found") {
      return "Bot cTrader này hiện không còn khả dụng trong public beta.";
    }
    if (detail === "contract_bot_template_not_deployable") {
      return "Bot mẫu không thể bật trong môi trường public.";
    }
    if (detail === "deployment_symbol_missing") {
      return "Deployment cTrader này chưa có symbol cấu hình để đánh giá.";
    }
    if (detail === "ctrader_symbol_not_found") {
      return "Không tìm thấy symbol cấu hình trên tài khoản cTrader hiện tại.";
    }
    if (detail === "ctrader_symbol_name_required") {
      return "Bot cTrader đang thiếu symbol cấu hình để lấy dữ liệu thị trường.";
    }
    if (detail.includes("does not exist")) {
      return "Kết nối hoặc tài khoản đã thay đổi. Hãy làm mới danh sách và thử lại.";
    }
    if (detail.includes("OAuth callback state could not be validated")) {
      return "Phiên kết nối cTrader đã hết hạn. Hãy quay lại và kết nối lại từ đầu.";
    }
    if (detail.startsWith("ctrader_backend_")) {
      return "Dịch vụ cTrader đang tạm gián đoạn. Vui lòng thử lại sau.";
    }
    return detail;
  }
  return "Hiện chưa thể xử lý yêu cầu này.";
}

function formatBrokerLabel(value?: string | null): string {
  const normalized = normalizeBrokerName(value);
  if (normalized === "icmarkets") {
    return "IC Markets";
  }
  return (value || "").trim() || "Broker";
}

function formatCTraderLimitationLabel(value: string): string {
  switch (value) {
    case "session_pool_multi_worker_coordination_not_implemented":
      return "Runtime cTrader chưa mở điều phối đa worker.";
    case "bot_execution_orchestrator_not_implemented":
      return "Public beta hiện hỗ trợ arm, đánh giá thủ công và audit; vòng tự trade liên tục vẫn chưa mở.";
    case "template_only_contract_catalog":
      return "Catalog hiện chỉ có bot mẫu, chưa có bot public khả dụng.";
    case "no_ctrader_contract_bots_found":
      return "Chưa có bot cTrader public nào sẵn sàng.";
    default:
      return value;
  }
}

function formatCTraderReasonLabel(value?: string | null): string {
  switch ((value || "").trim()) {
    case "signal_not_confirmed":
      return "Tín hiệu chưa đủ xác nhận.";
    case "not_enough_bars":
      return "Chưa đủ dữ liệu nến để đánh giá.";
    case "macd_support_resistance_confirmation":
      return "MACD và vùng hỗ trợ/kháng cự đã xác nhận tín hiệu.";
    default:
      return value?.trim() || "Không có ghi chú.";
  }
}

function formatCTraderEventTypeLabel(value?: string | null): string {
  switch ((value || "").trim()) {
    case "armed":
      return "Đã arm";
    case "evaluated":
      return "Đã đánh giá";
    case "session_degraded":
      return "Phiên cTrader lỗi";
    case "session_recovered":
      return "Phiên cTrader ổn lại";
    case "stopped":
      return "Đã dừng";
    default:
      return value?.trim() || "Sự kiện";
  }
}

function formatCTraderDeploymentStatusLabel(value?: string | null): string {
  const normalized = (value || "").trim().toLowerCase();
  switch (normalized) {
    case "ok":
      return "Sẵn sàng";
    case "online":
      return "Online";
    case "armed":
      return "Armed";
    case "started":
      return "Đã bật";
    case "degraded":
      return "Mất ổn định";
    case "stopped":
      return "Đã dừng";
    case "draft":
      return "Nháp";
    case "starting":
      return "Đang khởi tạo";
    case "partial":
      return "Khởi tạo một phần";
    case "disabled":
      return "Đang tắt";
    case "offline":
      return "Offline";
    default:
      return value?.trim() || "Chưa rõ";
  }
}

function formatCTraderCoordinatorStatusLabel(value?: string | null): string {
  switch ((value || "").trim()) {
    case "leader":
      return "Leader";
    case "standby":
      return "Standby";
    case "offline":
      return "Offline";
    default:
      return value?.trim() || "Chưa rõ";
  }
}

function formatCTraderDecisionLabel(value?: string | null): string {
  const normalized = (value || "").trim().toLowerCase();
  switch (normalized) {
    case "buy":
      return "Buy";
    case "sell":
      return "Sell";
    case "hold":
      return "Hold";
    case "armed":
      return "Armed";
    case "stopped":
      return "Stopped";
    default:
      return value?.trim() || "Chưa có";
  }
}

function formatCTraderEventStatusLabel(value?: string | null): string {
  const normalized = (value || "").trim().toLowerCase();
  if (["buy", "sell", "hold", "armed", "stopped"].includes(normalized)) {
    return formatCTraderDecisionLabel(normalized);
  }
  return formatCTraderDeploymentStatusLabel(normalized);
}

type CTraderLastEvaluation = {
  action?: string | null;
  reason?: string | null;
  signal_bar_time?: string | null;
  evaluated_at?: string | null;
  market_source?: string | null;
  symbol_name?: string | null;
  bars_count?: number | null;
  execution_supported?: boolean;
  execute_orders_requested?: boolean;
};

type CTraderRuntimeReconcile = {
  session_status?: string | null;
  last_checked_at?: string | null;
  last_ok_at?: string | null;
  latency_ms?: number | null;
  error?: string | null;
  broker_name?: string | null;
  account_number?: string | null;
};

function readCTraderLastEvaluation(deployment: CTraderDeployment | null): CTraderLastEvaluation | null {
  if (!deployment) {
    return null;
  }
  const rawMetadata = deployment.metadata_json;
  if (!rawMetadata || typeof rawMetadata !== "object" || Array.isArray(rawMetadata)) {
    return null;
  }
  const rawValue = rawMetadata.last_evaluation;
  if (!rawValue || typeof rawValue !== "object" || Array.isArray(rawValue)) {
    return null;
  }
  const value = rawValue as Record<string, unknown>;
  return {
    action: typeof value.action === "string" ? value.action : null,
    reason: typeof value.reason === "string" ? value.reason : null,
    signal_bar_time: typeof value.signal_bar_time === "string" ? value.signal_bar_time : null,
    evaluated_at: typeof value.evaluated_at === "string" ? value.evaluated_at : null,
    market_source: typeof value.market_source === "string" ? value.market_source : null,
    symbol_name: typeof value.symbol_name === "string" ? value.symbol_name : null,
    bars_count: typeof value.bars_count === "number" ? value.bars_count : null,
    execution_supported: value.execution_supported === true,
    execute_orders_requested: value.execute_orders_requested === true,
  };
}

function readCTraderRuntimeReconcile(deployment: CTraderDeployment | null): CTraderRuntimeReconcile | null {
  if (!deployment) {
    return null;
  }
  const rawMetadata = deployment.metadata_json;
  if (!rawMetadata || typeof rawMetadata !== "object" || Array.isArray(rawMetadata)) {
    return null;
  }
  const rawValue = rawMetadata.runtime_reconcile;
  if (!rawValue || typeof rawValue !== "object" || Array.isArray(rawValue)) {
    return null;
  }
  const value = rawValue as Record<string, unknown>;
  return {
    session_status: typeof value.session_status === "string" ? value.session_status : null,
    last_checked_at: typeof value.last_checked_at === "string" ? value.last_checked_at : null,
    last_ok_at: typeof value.last_ok_at === "string" ? value.last_ok_at : null,
    latency_ms: typeof value.latency_ms === "number" ? value.latency_ms : null,
    error: typeof value.error === "string" ? value.error : null,
    broker_name: typeof value.broker_name === "string" ? value.broker_name : null,
    account_number: typeof value.account_number === "string" ? value.account_number : null,
  };
}

function getMetadataString(connection: CTraderBrokerConnection | null, key: string): string | null {
  if (!connection) {
    return null;
  }
  const value = connection.metadata_json[key];
  return typeof value === "string" ? value : null;
}

function formatDateLabel(value?: string | null): string {
  if (!value) {
    return "Chưa có";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function formatExpiryLabel(value?: string | null): string {
  if (!value) {
    return "Không rõ hạn token";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const diffMs = parsed.getTime() - Date.now();
  const base = formatDateLabel(value);

  if (diffMs <= 0) {
    return `${base} · có thể đã hết hạn`;
  }
  if (diffMs <= 60 * 60 * 1000) {
    return `${base} · còn dưới 1 giờ`;
  }
  if (diffMs <= 24 * 60 * 60 * 1000) {
    return `${base} · còn khoảng ${Math.ceil(diffMs / (60 * 60 * 1000))} giờ`;
  }
  return `${base} · còn khoảng ${Math.ceil(diffMs / (24 * 60 * 60 * 1000))} ngày`;
}

type PersistedCTraderDefaultSelection = {
  trading_account_id: string;
  broker_connection_id?: string;
  external_account_id?: string;
  environment?: string;
  account_number?: string | null;
  broker_name?: string | null;
  live_risk_confirmed?: boolean;
  selected_at?: string;
};

function getPersistedCTraderDefaultSelection(
  connection: CTraderBrokerConnection | null
): PersistedCTraderDefaultSelection | null {
  if (!connection) {
    return null;
  }
  const rawValue = connection.metadata_json?.default_account_selection;
  if (!rawValue || typeof rawValue !== "object" || Array.isArray(rawValue)) {
    return null;
  }
  const selection = rawValue as Record<string, unknown>;
  if (typeof selection.trading_account_id !== "string" || !selection.trading_account_id.trim()) {
    return null;
  }
  return {
    trading_account_id: selection.trading_account_id.trim(),
    broker_connection_id:
      typeof selection.broker_connection_id === "string" ? selection.broker_connection_id : undefined,
    external_account_id:
      typeof selection.external_account_id === "string" ? selection.external_account_id : undefined,
    environment: typeof selection.environment === "string" ? selection.environment : undefined,
    account_number: typeof selection.account_number === "string" ? selection.account_number : null,
    broker_name: typeof selection.broker_name === "string" ? selection.broker_name : null,
    live_risk_confirmed: selection.live_risk_confirmed === true,
    selected_at: typeof selection.selected_at === "string" ? selection.selected_at : undefined,
  };
}

function buildCTraderSelectionStorageKey(tenantUserId: string | null): string | null {
  if (!tenantUserId) {
    return null;
  }
  return `cntx:ctrader:selected-account:${tenantUserId}`;
}

function readStoredCTraderAccountId(tenantUserId: string | null): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const storageKey = buildCTraderSelectionStorageKey(tenantUserId);
  if (!storageKey) {
    return null;
  }
  const value = window.localStorage.getItem(storageKey);
  return value && value.trim() ? value.trim() : null;
}

function writeStoredCTraderAccountId(tenantUserId: string | null, accountId: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  const storageKey = buildCTraderSelectionStorageKey(tenantUserId);
  if (!storageKey) {
    return;
  }
  if (!accountId) {
    window.localStorage.removeItem(storageKey);
    return;
  }
  window.localStorage.setItem(storageKey, accountId);
}

function normalizeBrokerName(value?: string | null): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function brokerNamesMatch(left?: string | null, right?: string | null): boolean {
  const leftValue = normalizeBrokerName(left);
  const rightValue = normalizeBrokerName(right);
  return Boolean(leftValue) && Boolean(rightValue) && leftValue === rightValue;
}

function isServerPendingBroker(brokerName?: string | null): boolean {
  return SERVER_PENDING_BROKER_NAMES.some((pendingBroker) => brokerNamesMatch(brokerName, pendingBroker));
}

function getFixedMt5Server(brokerName?: string | null): string | null {
  return brokerNamesMatch(brokerName, DBG_MARKETS_BROKER_NAME) ? DBG_MARKETS_FIXED_SERVER : null;
}

function isReadyMt5Account(account: MT5AccountItem): boolean {
  const status = String(account.status || "").trim().toLowerCase();
  return status === "connected" || Boolean(account.verified_at) || (status === "pending_verification" && Boolean(account.has_credentials));
}

function sortCTraderConnections(
  connections: CTraderBrokerConnection[]
): CTraderBrokerConnection[] {
  return [...connections].sort((left, right) => {
    const leftTs = new Date(left.updated_at || left.created_at).getTime();
    const rightTs = new Date(right.updated_at || right.created_at).getTime();
    return rightTs - leftTs;
  });
}

function pickBestCTraderConnection(
  connections: CTraderBrokerConnection[],
  accounts: CTraderTradingAccount[],
  preferredBrokerName?: string | null
): CTraderBrokerConnection | null {
  const sortedConnections = sortCTraderConnections(connections);
  if (!sortedConnections.length) {
    return null;
  }
  if (!preferredBrokerName) {
    return sortedConnections[0] ?? null;
  }

  const directMatch = sortedConnections.find((connection) => {
    const defaultSelection = getPersistedCTraderDefaultSelection(connection);
    if (brokerNamesMatch(defaultSelection?.broker_name, preferredBrokerName)) {
      return true;
    }
    return accounts.some(
      (account) =>
        account.broker_connection_id === connection.id && brokerNamesMatch(account.broker_name, preferredBrokerName)
    );
  });
  return directMatch ?? sortedConnections[0] ?? null;
}

function pickFallbackCTraderAccount(
  accounts: CTraderTradingAccount[],
  preferredBrokerName: string | null,
  storedAccountId: string | null,
  persistedSelection: PersistedCTraderDefaultSelection | null
): CTraderTradingAccount | null {
  if (!accounts.length) {
    return null;
  }

  const storedAccount =
    storedAccountId != null ? accounts.find((account) => account.id === storedAccountId) ?? null : null;
  if (storedAccount) {
    return storedAccount;
  }

  const persistedAccount =
    persistedSelection?.trading_account_id != null
      ? accounts.find((account) => account.id === persistedSelection.trading_account_id) ?? null
      : null;
  if (persistedAccount) {
    return persistedAccount;
  }

  const preferredAccount =
    preferredBrokerName != null
      ? accounts.find((account) => brokerNamesMatch(account.broker_name, preferredBrokerName)) ?? null
      : null;
  return preferredAccount ?? accounts[0] ?? null;
}

export default function BotPage() {
  const [form, setForm] = useState<FormState>({
    broker: "",
    server: "",
    login: "",
    password: "",
    label: "",
  });
  const [telegramTenantUserId, setTelegramTenantUserId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [mt5VerificationPhase, setMt5VerificationPhase] = useState<Mt5VerificationPhase | null>(null);
  const [connectingCTrader, setConnectingCTrader] = useState(false);
  const [loadingCTraderState, setLoadingCTraderState] = useState(false);
  const [syncingCTraderAccounts, setSyncingCTraderAccounts] = useState(false);
  const [refreshingCTraderToken, setRefreshingCTraderToken] = useState(false);
  const [loadingMt5Bots, setLoadingMt5Bots] = useState(false);
  const [mt5Bots, setMt5Bots] = useState<MT5BotCatalogItem[]>([]);
  const [mt5BotCatalogError, setMt5BotCatalogError] = useState<string | null>(null);
  const [selectedMt5BotName, setSelectedMt5BotName] = useState("");
  const [mt5BotTokenInput, setMt5BotTokenInput] = useState("");
  const [mt5WorkspaceTab, setMt5WorkspaceTab] = useState<Mt5WorkspaceTab>("connect");
  const [ctraderEnvironmentFilter, setCTraderEnvironmentFilter] = useState<CTraderEnvironmentFilter>("all");
  const [selectedCTraderAccountId, setSelectedCTraderAccountId] = useState<string | null>(null);
  const [liveRiskConfirmed, setLiveRiskConfirmed] = useState(false);
  const [savingCTraderSelection, setSavingCTraderSelection] = useState(false);
  const [ctraderConnection, setCTraderConnection] = useState<CTraderBrokerConnection | null>(null);
  const [ctraderAccounts, setCTraderAccounts] = useState<CTraderTradingAccount[]>([]);
  const [loadingCTraderRuntime, setLoadingCTraderRuntime] = useState(false);
  const [startingCTraderDeployment, setStartingCTraderDeployment] = useState(false);
  const [stoppingCTraderDeployment, setStoppingCTraderDeployment] = useState(false);
  const [evaluatingCTraderDeployment, setEvaluatingCTraderDeployment] = useState(false);
  const [ctraderBots, setCTraderBots] = useState<CTraderBotCatalogItem[]>([]);
  const [ctraderBotBlockers, setCTraderBotBlockers] = useState<string[]>([]);
  const [selectedCTraderBotCode, setSelectedCTraderBotCode] = useState("");
  const [ctraderDeployments, setCTraderDeployments] = useState<CTraderDeployment[]>([]);
  const [ctraderDeploymentDetail, setCTraderDeploymentDetail] = useState<CTraderDeployment | null>(null);
  const [ctraderDeploymentEvents, setCTraderDeploymentEvents] = useState<CTraderDeploymentEvent[]>([]);
  const [ctraderOverview, setCTraderOverview] = useState<CTraderBetaOverview | null>(null);
  const [miniappAccess, setMiniappAccess] = useState<{ mt5_full_access: boolean; bot_token_required: boolean } | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [ctraderOnboardingTarget, setCTraderOnboardingTarget] = useState<CTraderOnboardingTarget | null>(null);
  const [ctraderFocusHighlight, setCTraderFocusHighlight] = useState<CTraderOnboardingTarget | null>(null);
  const {
    termsVersion,
    termsModalOpen,
    termsAccepting,
    termsError,
    termsEnabled,
    requireTerms,
    acceptTerms,
  } = useMiniappTerms();
  const ctraderAccountListRef = useRef<HTMLDivElement | null>(null);
  const ctraderSelectionRef = useRef<HTMLDivElement | null>(null);
  const ctraderControlRef = useRef<HTMLDivElement | null>(null);

  const resolvedBroker = form.broker.trim();
  const selectedBrokerPreset =
    brokerPresets.find((preset) => brokerNamesMatch(preset.name, resolvedBroker)) ?? null;
  const selectedLane = selectedBrokerPreset?.lane ?? null;
  const selectedBrokerIsIcMarkets = brokerNamesMatch(selectedBrokerPreset?.name, "ICMarkets");
  const brokerDisplayName = formatBrokerLabel(selectedBrokerPreset?.name ?? resolvedBroker);
  const selectedBrokerServerPending = isServerPendingBroker(resolvedBroker);
  const selectedBrokerFixedServer = getFixedMt5Server(resolvedBroker);
  const selectedMt5Bot =
    mt5Bots.find((bot) => bot.bot_name === selectedMt5BotName) ??
    mt5Bots.find((bot) => bot.bot_id === selectedMt5BotName) ??
    null;
  const mt5BotToken = mt5BotTokenInput.trim();
  const mt5FullAccess = miniappAccess?.mt5_full_access === true;
  const mt5BotTokenRequired = miniappAccess?.bot_token_required !== false;
  const mt5BotCatalogLoadingEmpty = loadingMt5Bots && mt5Bots.length === 0;
  const mt5LoginPrerequisitesReady = Boolean(selectedMt5Bot && (!mt5BotTokenRequired || mt5BotToken));
  const persistedDefaultCTraderSelection = getPersistedCTraderDefaultSelection(ctraderConnection);
  const filteredCTraderAccounts =
    ctraderEnvironmentFilter === "all"
      ? ctraderAccounts
      : ctraderAccounts.filter((account) => account.environment === ctraderEnvironmentFilter);
  const activeCTraderAccountPool =
    ctraderEnvironmentFilter === "all" ? ctraderAccounts : filteredCTraderAccounts;
  const selectedCTraderAccount =
    activeCTraderAccountPool.find((account) => account.id === selectedCTraderAccountId) ??
    activeCTraderAccountPool[0] ??
    null;
  const persistedCTraderAccount =
    ctraderAccounts.find((account) => account.id === selectedCTraderAccountId) ?? selectedCTraderAccount;
  const selectedCTraderBot =
    ctraderBots.find((bot) => bot.bot_code === selectedCTraderBotCode) ?? ctraderBots[0] ?? null;
  const selectedCTraderEnvironment = selectedCTraderAccount?.environment ?? null;
  const selectedCTraderRuntimeAccountId = selectedCTraderAccount?.id ?? null;
  const activeCTraderDeployment =
    selectedCTraderAccount == null
      ? null
      : ctraderDeployments.find(
          (deployment) =>
            deployment.trading_account_id === selectedCTraderAccount.id && deployment.desired_state === "started"
        ) ?? null;
  const cTraderSelectionReady =
    Boolean(ctraderConnection) &&
    Boolean(selectedCTraderAccount) &&
    (selectedCTraderEnvironment !== "live" || liveRiskConfirmed);
  const ctraderRuntimeLimitations = ctraderBotBlockers.map(formatCTraderLimitationLabel);
  const ctraderOverviewLimitations = (ctraderOverview?.blockers ?? ctraderBotBlockers).map(
    formatCTraderLimitationLabel
  );
  const latestCTraderEvaluationEvent =
    ctraderDeploymentEvents.find((event) => event.event_type === "evaluated") ?? null;
  const latestCTraderDecision =
    latestCTraderEvaluationEvent &&
    latestCTraderEvaluationEvent.payload_json &&
    typeof latestCTraderEvaluationEvent.payload_json === "object" &&
    !Array.isArray(latestCTraderEvaluationEvent.payload_json)
      ? (latestCTraderEvaluationEvent.payload_json.decision as Record<string, unknown> | undefined)
      : undefined;
  const activeCTraderDeploymentDetail =
    ctraderDeploymentDetail && activeCTraderDeployment && ctraderDeploymentDetail.id === activeCTraderDeployment.id
      ? ctraderDeploymentDetail
      : activeCTraderDeployment;
  const latestCTraderStoredEvaluation = readCTraderLastEvaluation(activeCTraderDeploymentDetail ?? null);
  const latestCTraderRuntimeReconcile = readCTraderRuntimeReconcile(activeCTraderDeploymentDetail ?? null);
  const latestCTraderDecisionAction =
    (typeof latestCTraderStoredEvaluation?.action === "string" && latestCTraderStoredEvaluation.action) ||
    (typeof latestCTraderDecision?.action === "string" && latestCTraderDecision.action) ||
    latestCTraderEvaluationEvent?.event_status ||
    null;
  const latestCTraderDecisionReason =
    (typeof latestCTraderStoredEvaluation?.reason === "string" && latestCTraderStoredEvaluation.reason) ||
    (typeof latestCTraderDecision?.reason === "string" && latestCTraderDecision.reason) ||
    (typeof latestCTraderEvaluationEvent?.payload_json?.reason === "string"
      ? latestCTraderEvaluationEvent.payload_json.reason
      : null);
  const latestCTraderDecisionAt =
    latestCTraderStoredEvaluation?.evaluated_at ||
    latestCTraderEvaluationEvent?.occurred_at ||
    latestCTraderEvaluationEvent?.created_at ||
    null;
  const latestCTraderEvaluationSnapshotLabel =
    latestCTraderStoredEvaluation?.symbol_name && latestCTraderStoredEvaluation?.bars_count
      ? `${latestCTraderStoredEvaluation.symbol_name} · ${latestCTraderStoredEvaluation.bars_count} nến`
      : latestCTraderStoredEvaluation?.symbol_name || null;
  const isPersistedDefaultCTraderAccount =
    Boolean(selectedCTraderAccount) &&
    persistedDefaultCTraderSelection?.trading_account_id === selectedCTraderAccount?.id;
  const ctraderStartReady =
    Boolean(selectedCTraderAccount) &&
    Boolean(selectedCTraderBot) &&
    Boolean(ctraderConnection) &&
    (selectedCTraderEnvironment !== "live" || liveRiskConfirmed);
  const ctraderAccountListFocusClass =
    ctraderFocusHighlight === "account_list"
      ? "ring-2 ring-cyan-300/30 shadow-[0_0_0_1px_rgba(103,232,249,0.08)]"
      : "";
  const ctraderSelectionFocusClass =
    ctraderFocusHighlight === "selection"
      ? "ring-2 ring-cyan-300/30 shadow-[0_0_0_1px_rgba(103,232,249,0.08)]"
      : "";
  const ctraderControlFocusClass =
    ctraderFocusHighlight === "control"
      ? "ring-2 ring-cyan-300/30 shadow-[0_0_0_1px_rgba(103,232,249,0.08)]"
      : "";

  const loadCTraderState = useCallback(
    async (options?: { silentErrors?: boolean }) => {
      if (selectedBrokerIsIcMarkets || !telegramTenantUserId) {
        setCTraderConnection(null);
        setCTraderAccounts([]);
        return;
      }

      setLoadingCTraderState(true);

      try {
        const [connectionsResponse, accountsResponse] = await Promise.all([
          fetchCTraderConnections(telegramTenantUserId),
          fetchCTraderAccounts(telegramTenantUserId),
        ]);

        const preferredBrokerName = selectedBrokerPreset?.name ?? resolvedBroker ?? null;
        const latestConnection = pickBestCTraderConnection(
          connectionsResponse.items,
          accountsResponse.items,
          preferredBrokerName
        );
        const accounts = latestConnection
          ? accountsResponse.items.filter((item) => item.broker_connection_id === latestConnection.id)
          : accountsResponse.items;

        setCTraderConnection(latestConnection);
        setCTraderAccounts(accounts);
      } catch (error) {
        if (!options?.silentErrors) {
          setNotice({
            tone: "error",
            message: getErrorMessage(error),
          });
        }
      } finally {
        setLoadingCTraderState(false);
      }
    },
    [resolvedBroker, selectedBrokerIsIcMarkets, selectedBrokerPreset, telegramTenantUserId]
  );

  const loadCTraderRuntimeState = useCallback(
    async (options?: { silentErrors?: boolean }) => {
      if (selectedLane !== "ctrader" || selectedBrokerIsIcMarkets) {
        setCTraderBots([]);
        setCTraderBotBlockers([]);
        setCTraderDeployments([]);
        setCTraderDeploymentDetail(null);
        setCTraderDeploymentEvents([]);
        setCTraderOverview(null);
        return;
      }

      setLoadingCTraderRuntime(true);

      try {
        const runtimeState = await fetchCTraderRuntimeState(selectedCTraderRuntimeAccountId ?? undefined, 8);

        setCTraderOverview(runtimeState.overview);
        setCTraderBots(runtimeState.bot_catalog.items);
        setCTraderBotBlockers(runtimeState.bot_catalog.blockers || []);
        setCTraderDeployments(runtimeState.deployments.items);
        setCTraderDeploymentDetail(runtimeState.deployment_detail ?? runtimeState.active_deployment ?? null);
        setCTraderDeploymentEvents(runtimeState.deployment_events.items);
      } catch (error) {
        if (!options?.silentErrors) {
          setNotice({
            tone: "error",
            message: getErrorMessage(error),
          });
        }
      } finally {
        setLoadingCTraderRuntime(false);
      }
    },
    [selectedBrokerIsIcMarkets, selectedCTraderRuntimeAccountId, selectedLane]
  );

  useEffect(() => {
    let cancelled = false;

    const initialTenantUserId = getTelegramTenantUserId();
    setTelegramTenantUserId(initialTenantUserId);
    if (!initialTenantUserId) {
      void waitForTelegramTenantUserId().then((tenantUserId) => {
        if (!cancelled && tenantUserId) {
          setTelegramTenantUserId(tenantUserId);
        }
      });
    }

    void fetchMiniappAccess()
      .then((access) => {
        if (!cancelled) {
          setMiniappAccess(access);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMiniappAccess(null);
        }
      });

    if (typeof window === "undefined") {
      return () => {
        cancelled = true;
      };
    }

    const searchParams = new URLSearchParams(window.location.search);
    const brokerParam = searchParams.get("broker");
    const laneParam = searchParams.get("lane");
    const connectedFlag = searchParams.get("connected");
    const accountsParam = Number(searchParams.get("accounts") || "0");
    const nextActionParam = searchParams.get("next_action");
    const preferredAccountIdParam = searchParams.get("trading_account_id");
    const preferredEnvironmentParam = searchParams.get("account_env");
    const openedFromLegacyTelegramEntry = searchParams.has("tg") || searchParams.has("sig");
    const internalBotNavAt = Number(window.sessionStorage.getItem(INTERNAL_BOT_NAV_MARKER) || "0");
    const openedFromInternalBotTab = Number.isFinite(internalBotNavAt) && Date.now() - internalBotNavAt < 15_000;

    window.sessionStorage.removeItem(INTERNAL_BOT_NAV_MARKER);

    if (
      openedFromLegacyTelegramEntry &&
      !openedFromInternalBotTab &&
      !brokerParam &&
      !laneParam &&
      !connectedFlag &&
      !nextActionParam &&
      !preferredAccountIdParam &&
      !preferredEnvironmentParam
    ) {
      const homeUrl = new URL("/", window.location.origin);
      homeUrl.search = searchParams.toString();
      window.location.replace(homeUrl.toString());
      return () => {
        cancelled = true;
      };
    }

    const matchedBroker =
      brokerPresets.find((preset) => brokerNamesMatch(preset.name, brokerParam)) ??
      (laneParam === "ctrader" ? brokerPresets.find((preset) => brokerNamesMatch(preset.name, "ICMarkets")) ?? null : null);

    if (matchedBroker) {
      setForm((current) => ({
        ...current,
        broker: matchedBroker.name,
      }));
    } else {
      const storedMt5Broker = readStoredMt5Broker();
      const storedBrokerPreset =
        storedMt5Broker == null
          ? null
          : brokerPresets.find(
              (preset) => preset.lane === "mt5" && brokerNamesMatch(preset.name, storedMt5Broker)
            ) ?? null;

      if (storedBrokerPreset) {
        setForm((current) => ({
          ...current,
          broker: storedBrokerPreset.name,
        }));
      }
    }

    if (preferredAccountIdParam) {
      setSelectedCTraderAccountId(preferredAccountIdParam);
    }

    if (preferredEnvironmentParam === "live" || preferredEnvironmentParam === "demo") {
      setCTraderEnvironmentFilter(preferredEnvironmentParam);
    }

    if (laneParam === "ctrader" && nextActionParam) {
      if (nextActionParam === "confirm_live_risk") {
        setCTraderOnboardingTarget("selection");
      } else if (nextActionParam === "ready") {
        setCTraderOnboardingTarget("control");
      } else {
        setCTraderOnboardingTarget("account_list");
      }
    }

    if (connectedFlag === "1") {
      const brokerNoticeLabel = formatBrokerLabel(matchedBroker?.name ?? brokerParam ?? "cTrader");
      setNotice({
        tone: "success",
        message:
          nextActionParam === "ready"
            ? `${brokerNoticeLabel} đã sẵn sàng. Mini App đã khôi phục account để bạn tiếp tục ngay.`
            : nextActionParam === "confirm_live_risk"
              ? `${brokerNoticeLabel} đã kết nối xong. Hãy xác nhận tài khoản live trước khi bật bot.`
              : nextActionParam === "select_account"
                ? accountsParam > 1
                  ? `${brokerNoticeLabel} đã kết nối xong. Hãy chọn tài khoản bạn muốn dùng trước khi tiếp tục.`
                  : accountsParam === 1
                    ? `${brokerNoticeLabel} đã kết nối xong. Hãy kiểm tra account vừa đồng bộ.`
                    : `${brokerNoticeLabel} đã kết nối xong. Danh sách tài khoản có thể vẫn đang đồng bộ.`
                : accountsParam > 0
                  ? `${brokerNoticeLabel} đã kết nối xong. Có ${accountsParam} tài khoản.`
                  : `${brokerNoticeLabel} đã kết nối xong.`,
      });
    }

    if (
      brokerParam ||
      laneParam ||
      connectedFlag ||
      searchParams.has("next_action") ||
      searchParams.has("trading_account_id") ||
      searchParams.has("account_env")
    ) {
      const cleanUrl = new URL(window.location.href);
      cleanUrl.search = "";
      window.history.replaceState({}, "", cleanUrl.toString());
    }
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedLane === "ctrader" && !selectedBrokerIsIcMarkets && telegramTenantUserId) {
      void loadCTraderState({ silentErrors: true });
    }
  }, [loadCTraderState, selectedBrokerIsIcMarkets, selectedLane, telegramTenantUserId]);

  useEffect(() => {
    if (selectedLane === "ctrader" && !selectedBrokerIsIcMarkets) {
      void loadCTraderRuntimeState({ silentErrors: true });
    }
  }, [loadCTraderRuntimeState, selectedBrokerIsIcMarkets, selectedLane]);

  useEffect(() => {
    if (selectedLane !== "ctrader" || selectedBrokerIsIcMarkets || !activeCTraderDeployment?.id) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadCTraderRuntimeState({ silentErrors: true });
    }, 15000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeCTraderDeployment?.id, loadCTraderRuntimeState, selectedBrokerIsIcMarkets, selectedLane]);

  useEffect(() => {
    if (selectedLane !== "ctrader" || selectedBrokerIsIcMarkets || !ctraderOnboardingTarget) {
      return;
    }

    const targetElement =
      ctraderOnboardingTarget === "account_list"
        ? ctraderAccountListRef.current
        : ctraderOnboardingTarget === "selection"
          ? ctraderSelectionRef.current
          : ctraderControlRef.current;

    if (!targetElement) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      targetElement.scrollIntoView({ behavior: "smooth", block: "start" });
      setCTraderFocusHighlight(ctraderOnboardingTarget);
      setCTraderOnboardingTarget(null);
    }, 180);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [
    ctraderOnboardingTarget,
    ctraderAccounts.length,
    selectedCTraderAccount?.environment,
    selectedCTraderAccount?.id,
    selectedBrokerIsIcMarkets,
    selectedLane,
  ]);

  useEffect(() => {
    if (!ctraderFocusHighlight) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setCTraderFocusHighlight(null);
    }, 3500);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [ctraderFocusHighlight]);

  useEffect(() => {
    const storedAccountId = readStoredCTraderAccountId(telegramTenantUserId);
    if (storedAccountId) {
      setSelectedCTraderAccountId(storedAccountId);
    }
  }, [telegramTenantUserId]);

  useEffect(() => {
    if (!persistedDefaultCTraderSelection?.trading_account_id) {
      return;
    }
    setSelectedCTraderAccountId(persistedDefaultCTraderSelection.trading_account_id);
    setLiveRiskConfirmed(Boolean(persistedDefaultCTraderSelection.live_risk_confirmed));
  }, [
    persistedDefaultCTraderSelection?.live_risk_confirmed,
    persistedDefaultCTraderSelection?.trading_account_id,
  ]);

  useEffect(() => {
    if (selectedLane !== "mt5") {
      setMt5WorkspaceTab("connect");
      return;
    }

    if (!resolvedBroker) {
      setMt5WorkspaceTab("connect");
      return;
    }

    let cancelled = false;
    const brokerAtLoad = resolvedBroker;

    void (async () => {
      try {
        const response = await fetchMt5Accounts();
        if (cancelled) {
          return;
        }
        const hasReadyAccount = response.items.some(
          (account) => brokerNamesMatch(account.broker, brokerAtLoad) && isReadyMt5Account(account)
        );
        setMt5WorkspaceTab(hasReadyAccount ? "control" : "connect");
      } catch {
        if (!cancelled) {
          setMt5WorkspaceTab("connect");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [resolvedBroker, selectedLane]);

  const loadMt5BotCatalog = useCallback(async () => {
    setLoadingMt5Bots(true);
    try {
      const response = await fetchMt5BotCatalog();
      setMt5Bots(response.items);
      setMt5BotCatalogError(null);
    } catch (error) {
      setMt5BotCatalogError(getErrorMessage(error));
    } finally {
      setLoadingMt5Bots(false);
    }
  }, []);

  useEffect(() => {
    if (selectedLane !== "mt5") {
      setMt5Bots([]);
      setMt5BotCatalogError(null);
      setSelectedMt5BotName("");
      setMt5BotTokenInput("");
      return;
    }

    void loadMt5BotCatalog();
  }, [loadMt5BotCatalog, selectedLane]);

  useEffect(() => {
    if (!mt5Bots.length) {
      setSelectedMt5BotName("");
      return;
    }

    if (!selectedMt5BotName) {
      const preferredBot = mt5Bots.find((bot) => bot.display_name === "Gs Algo") ?? mt5Bots[0];
      setSelectedMt5BotName(preferredBot.bot_name);
      return;
    }

    if (selectedMt5BotName && !mt5Bots.some((bot) => bot.bot_name === selectedMt5BotName)) {
      setSelectedMt5BotName("");
      setMt5BotTokenInput("");
    }
  }, [mt5Bots, selectedMt5BotName]);

  useEffect(() => {
    if (selectedBrokerFixedServer) {
      if (form.server !== selectedBrokerFixedServer) {
        setForm((current) => ({
          ...current,
          server: selectedBrokerFixedServer,
        }));
      }
      return;
    }

    if (!selectedBrokerServerPending) {
      if (form.server === DBG_MARKETS_SERVER_PENDING_LABEL || form.server === DBG_MARKETS_FIXED_SERVER) {
        setForm((current) => ({
          ...current,
          server: "",
        }));
      }
      return;
    }

    if (form.server !== DBG_MARKETS_SERVER_PENDING_LABEL) {
      setForm((current) => ({
        ...current,
        server: DBG_MARKETS_SERVER_PENDING_LABEL,
      }));
    }
  }, [form.server, selectedBrokerFixedServer, selectedBrokerServerPending]);

  useEffect(() => {
    if (!ctraderAccounts.length) {
      setSelectedCTraderAccountId(null);
      setLiveRiskConfirmed(false);
      return;
    }

    if (selectedCTraderAccountId && ctraderAccounts.some((account) => account.id === selectedCTraderAccountId)) {
      return;
    }

    const storedAccountId = readStoredCTraderAccountId(telegramTenantUserId);
    const preferredBrokerName = selectedBrokerPreset?.name ?? resolvedBroker ?? null;
    const fallbackAccount = pickFallbackCTraderAccount(
      ctraderAccounts,
      preferredBrokerName,
      storedAccountId,
      persistedDefaultCTraderSelection
    );

    setSelectedCTraderAccountId(fallbackAccount?.id ?? null);
  }, [
    ctraderAccounts,
    persistedDefaultCTraderSelection,
    resolvedBroker,
    selectedBrokerPreset,
    selectedCTraderAccountId,
    telegramTenantUserId,
  ]);

  useEffect(() => {
    if (!selectedCTraderAccount) {
      setLiveRiskConfirmed(false);
      return;
    }
    if (selectedCTraderAccount.environment !== "live") {
      setLiveRiskConfirmed(false);
    }
  }, [selectedCTraderAccount]);

  useEffect(() => {
    if (!ctraderAccounts.length) {
      writeStoredCTraderAccountId(telegramTenantUserId, null);
      return;
    }
    if (persistedCTraderAccount) {
      writeStoredCTraderAccountId(telegramTenantUserId, persistedCTraderAccount.id);
    }
  }, [ctraderAccounts.length, persistedCTraderAccount, telegramTenantUserId]);

  useEffect(() => {
    if (!selectedCTraderAccount) {
      return;
    }
    if (selectedCTraderAccountId !== selectedCTraderAccount.id) {
      setSelectedCTraderAccountId(selectedCTraderAccount.id);
    }
  }, [selectedCTraderAccount, selectedCTraderAccountId]);

  useEffect(() => {
    if (!ctraderBots.length) {
      setSelectedCTraderBotCode("");
      return;
    }
    if (selectedCTraderBotCode && ctraderBots.some((bot) => bot.bot_code === selectedCTraderBotCode)) {
      return;
    }
    setSelectedCTraderBotCode(ctraderBots[0]?.bot_code ?? "");
  }, [ctraderBots, selectedCTraderBotCode]);

  useEffect(() => {
    if (!activeCTraderDeployment?.bot_code) {
      return;
    }
    if (!ctraderBots.some((bot) => bot.bot_code === activeCTraderDeployment.bot_code)) {
      return;
    }
    if (selectedCTraderBotCode !== activeCTraderDeployment.bot_code) {
      setSelectedCTraderBotCode(activeCTraderDeployment.bot_code);
    }
  }, [activeCTraderDeployment?.bot_code, ctraderBots, selectedCTraderBotCode]);

  async function handleConnect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    const formElement = event.currentTarget;

    if (requireTerms(() => {
      window.setTimeout(() => formElement.requestSubmit(), 0);
    })) {
      return;
    }

    if (selectedLane !== "mt5") {
      return;
    }

    if (loadingMt5Bots) {
      setNotice({
        tone: "info",
        message: "Đang tải danh sách bot. Vui lòng chờ một chút.",
      });
      return;
    }

    if (!selectedMt5Bot) {
      setNotice({
        tone: "error",
        message: "Vui lòng chọn bot trước khi đăng nhập tài khoản MT5.",
      });
      return;
    }

    if (mt5BotTokenRequired && !mt5BotToken) {
      setNotice({
        tone: "error",
        message: "Vui lòng nhập token bot trước khi đăng nhập tài khoản MT5.",
      });
      return;
    }

    if (selectedBrokerServerPending) {
      setNotice({
        tone: "info",
        message: `Server ${resolvedBroker} đang cập nhật.`,
      });
      return;
    }

    const payload: ConnectMt5AccountRequest = {
      broker: resolvedBroker,
      server: selectedBrokerFixedServer ?? form.server.trim(),
      login: form.login.trim(),
      password: form.password,
      label: form.label.trim() || undefined,
    };

    if (!payload.broker) {
      setNotice({
        tone: "error",
        message: "Vui lòng chọn sàn giao dịch trước khi kết nối tài khoản MT5.",
      });
      return;
    }

    if (!payload.server || !payload.login || !payload.password) {
      setNotice({
        tone: "error",
        message: "Vui lòng nhập đầy đủ server, tài khoản và mật khẩu MT5.",
      });
      return;
    }

    setSubmitting(true);
    setMt5VerificationPhase("SUBMITTED");

    try {
      const connected = await connectMt5Account(payload);
      setMt5VerificationPhase("SUBMITTED");

      const verifyStarted = await requestMt5AccountVerification({ account_id: connected.account_id });
      const verifyJobId = Number(verifyStarted.job_id ?? verifyStarted.verification_job_id ?? verifyStarted.id ?? 0);
      if (!verifyJobId) {
        throw new BackendAPIError("Backend không trả verification_job_id sau khi tạo job xác minh.", {
          status: 502,
          code: "verification_job_id_missing",
        });
      }
      setMt5VerificationPhase("VERIFYING_MT5");
      const finalVerify = await pollMt5AccountVerificationJob(verifyJobId, { intervalMs: 2000, maxAttempts: 90 });
      const terminal = String(finalVerify.job_status ?? finalVerify.status ?? "").toLowerCase();
      if (terminal === "failed" || terminal === "cancelled") {
        const hint =
          (typeof finalVerify.last_error === "string" && finalVerify.last_error) ||
          (typeof finalVerify.user_message_key === "string" && finalVerify.user_message_key) ||
          "Xác minh MT5 thất bại.";
        throw new BackendAPIError(hint, { status: 409, code: "mt5_verification_failed" });
      }

      setMt5VerificationPhase("ASSIGNED");

      let claimedEntitlementId: string | undefined;
      if (mt5BotTokenRequired) {
        try {
          const claimResult = await claimMt5BotToken({
            account_id: connected.account_id,
            bot_name: selectedMt5Bot.bot_name,
            token: mt5BotToken,
          });
          claimedEntitlementId = claimResult.entitlement?.entitlement_id || undefined;
          if (!mt5FullAccess && !claimedEntitlementId) {
            setNotice({
              tone: "error",
              message: "Token đã được kiểm tra nhưng chưa mở được quyền bot. Vui lòng thử lại.",
            });
            return;
          }
        } catch (tokenError) {
          setNotice({
            tone: "error",
            message: getErrorMessage(tokenError),
          });
          return;
        }
      }

      setForm((current) => ({
        ...current,
        login: "",
        password: "",
        label: "",
      }));
      setMt5BotTokenInput("");
      setMt5VerificationPhase("VERIFIED");
      setNotice({
        tone: "success",
        message: `Đã lưu tài khoản. Bạn có thể bật bot ${selectedMt5Bot.display_name} ở panel điều khiển.`,
      });
      writeStoredMt5Broker(resolvedBroker);
      setMt5WorkspaceTab("control");
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setSubmitting(false);
      setMt5VerificationPhase(null);
    }
  }

  async function handleConnectCTrader() {
    setNotice(null);

    if (selectedLane !== "ctrader" || !selectedBrokerPreset) {
      return;
    }

    if (!telegramTenantUserId) {
      setNotice({
        tone: "error",
        message: "Lane cTrader cần Telegram user id. Hãy mở mini app trực tiếp trong Telegram rồi thử lại.",
      });
      return;
    }

    if (!selectedCTraderBot) {
      setNotice({
        tone: "error",
        message: "Chọn bot trước khi kết nối IC Markets.",
      });
      return;
    }

    if (typeof window === "undefined") {
      return;
    }

    setConnectingCTrader(true);

    try {
      const redirectUri = new URL("/bot/ctrader/callback", window.location.origin).toString();
      const response = await createCTraderAuthorizeUrl({
        tenant_user_id: telegramTenantUserId,
        redirect_uri: redirectUri,
        scope: "trading",
        state: selectedBrokerPreset.name,
      });

      window.location.assign(response.auth_url);
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
      setConnectingCTrader(false);
    }
  }

  async function handleRefreshCTraderToken() {
    if (!telegramTenantUserId || !ctraderConnection) {
      setNotice({
        tone: "error",
        message: "Chưa có connection cTrader để làm mới token.",
      });
      return;
    }

    setNotice(null);
    setRefreshingCTraderToken(true);

    try {
      const refreshed = await refreshCTraderConnection(ctraderConnection.id, telegramTenantUserId);
      setCTraderConnection(refreshed);
      await loadCTraderState({ silentErrors: true });
      setNotice({
        tone: "success",
        message: `Đã làm mới kết nối ${brokerDisplayName}.`,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setRefreshingCTraderToken(false);
    }
  }

  async function handleSyncCTraderAccounts() {
    if (!telegramTenantUserId || !ctraderConnection) {
      setNotice({
        tone: "error",
        message: `Hãy kết nối ${brokerDisplayName} trước.`,
      });
      return;
    }

    setNotice(null);
    setSyncingCTraderAccounts(true);

    try {
      const discovered = await discoverCTraderAccounts({
        tenant_user_id: telegramTenantUserId,
        broker_connection_id: ctraderConnection.id,
      });
      setCTraderAccounts(discovered.items);
      await loadCTraderState({ silentErrors: true });
      setNotice({
        tone: "success",
        message:
          discovered.items.length > 0
            ? `Đã cập nhật ${discovered.items.length} tài khoản.`
            : "Chưa thấy tài khoản nào.",
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setSyncingCTraderAccounts(false);
    }
  }

  function handleSelectCTraderAccount(accountId: string) {
    const nextAccount = ctraderAccounts.find((account) => account.id === accountId) ?? null;
    setSelectedCTraderAccountId(accountId);
    setLiveRiskConfirmed(false);
    setNotice({
      tone: "info",
      message:
        nextAccount?.environment === "live"
          ? "Bạn đang chọn tài khoản live. Cần xác nhận trước khi lưu."
          : "Đã chọn tài khoản demo.",
    });
  }

  async function handleConfirmCTraderSelection() {
    if (!selectedCTraderAccount) {
      setNotice({
        tone: "error",
        message: "Hãy chọn một tài khoản trước.",
      });
      return;
    }
    if (selectedCTraderAccount.environment === "live" && !liveRiskConfirmed) {
      setNotice({
        tone: "error",
        message: "Tài khoản live cần xác nhận trước khi lưu.",
      });
      return;
    }

    if (!ctraderConnection) {
      setNotice({
        tone: "error",
        message: `Hãy kết nối ${brokerDisplayName} trước khi lưu.`,
      });
      return;
    }

    setSavingCTraderSelection(true);
    setNotice(null);

    try {
      const updatedConnection = await selectDefaultCTraderAccount({
        broker_connection_id: ctraderConnection.id,
        trading_account_id: selectedCTraderAccount.id,
        live_risk_confirmed: liveRiskConfirmed,
      });
      setCTraderConnection(updatedConnection);
      await loadCTraderRuntimeState({ silentErrors: true });
      writeStoredCTraderAccountId(telegramTenantUserId, selectedCTraderAccount.id);
      setNotice({
        tone: "success",
        message: "Đã lưu account.",
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setSavingCTraderSelection(false);
    }
  }

  async function handleRefreshCTraderRuntime() {
    setNotice(null);
    await loadCTraderRuntimeState();
  }

  async function handleEvaluateCTraderDeployment() {
    if (!activeCTraderDeployment) {
      setNotice({
        tone: "error",
        message: "Hãy bật deployment cTrader beta trước khi đánh giá.",
      });
      return;
    }

    setNotice(null);
    setEvaluatingCTraderDeployment(true);

    try {
      const result = await evaluateCTraderDeployment(activeCTraderDeployment.id);
      setCTraderDeploymentDetail(result.deployment);
      setCTraderDeployments((current) =>
        current.map((item) => (item.id === result.deployment.id ? result.deployment : item))
      );
      setCTraderDeploymentEvents((current) => [result.event, ...current.filter((item) => item.id !== result.event.id)].slice(0, 8));
      await loadCTraderRuntimeState({ silentErrors: true });

      const decisionAction = typeof result.evaluation.result?.action === "string" ? result.evaluation.result.action : null;
      const decisionReason = typeof result.evaluation.result?.reason === "string" ? result.evaluation.result.reason : null;
      setNotice({
        tone: "success",
        message: `Đã đánh giá bot beta. Kết quả hiện tại: ${formatCTraderDecisionLabel(decisionAction)}. ${formatCTraderReasonLabel(
          decisionReason
        )}`,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setEvaluatingCTraderDeployment(false);
    }
  }

  async function handleStartCTraderDeployment() {
    if (!ctraderConnection || !selectedCTraderAccount) {
      setNotice({
        tone: "error",
        message: "Hãy kết nối và chọn tài khoản cTrader trước.",
      });
      return;
    }
    if (!selectedCTraderBot) {
      setNotice({
        tone: "error",
        message: "Chưa có bot cTrader khả dụng để bật.",
      });
      return;
    }
    if (selectedCTraderAccount.environment === "live" && !liveRiskConfirmed) {
      setNotice({
        tone: "error",
        message: "Tài khoản live cần xác nhận trước khi bật bot.",
      });
      return;
    }

    setNotice(null);
    setStartingCTraderDeployment(true);

    try {
      let brokerConnectionId = ctraderConnection.id;

      if (!isPersistedDefaultCTraderAccount) {
        setSavingCTraderSelection(true);
        const updatedConnection = await selectDefaultCTraderAccount({
          broker_connection_id: ctraderConnection.id,
          trading_account_id: selectedCTraderAccount.id,
          live_risk_confirmed: liveRiskConfirmed,
        });
        setCTraderConnection(updatedConnection);
        writeStoredCTraderAccountId(telegramTenantUserId, selectedCTraderAccount.id);
        brokerConnectionId = updatedConnection.id;
      }

      const result = await startCTraderDeployment({
        broker_connection_id: brokerConnectionId,
        trading_account_id: selectedCTraderAccount.id,
        bot_code: selectedCTraderBot.bot_code,
        config: {},
        live_risk_confirmed: liveRiskConfirmed,
        force_reconnect: false,
        reason: "miniapp_public_beta_start",
      });
      await loadCTraderRuntimeState({ silentErrors: true });
      setNotice({
        tone: "success",
        message:
          result.start_status === "already_armed"
            ? "Deployment cTrader beta đã được bật trước đó."
            : "Đã bật bot cho tài khoản này.",
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setSavingCTraderSelection(false);
      setStartingCTraderDeployment(false);
    }
  }

  async function handleStopCTraderDeployment() {
    if (!activeCTraderDeployment) {
      setNotice({
        tone: "error",
        message: "Chưa có bot nào đang bật để tắt.",
      });
      return;
    }

    setNotice(null);
    setStoppingCTraderDeployment(true);

    try {
      await stopCTraderDeployment(activeCTraderDeployment.id, {
        reason: "miniapp_public_beta_stop",
      });
      await loadCTraderRuntimeState({ silentErrors: true });
      setNotice({
        tone: "success",
        message: "Đã tắt bot.",
      });
    } catch (error) {
      setNotice({
        tone: "error",
        message: getErrorMessage(error),
      });
    } finally {
      setStoppingCTraderDeployment(false);
    }
  }

  const ctraderExpiryLabel = formatExpiryLabel(ctraderConnection?.expires_at);
  const ctraderDiscoveryLabel = formatDateLabel(getMetadataString(ctraderConnection, "last_discovered_at"));

  return (
    <>
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="min-h-screen min-h-[100dvh] px-4 pb-28 pt-6 app-bg"
      >
        <div className="relative z-10 mx-auto flex max-w-md flex-col gap-5">
          <PageHeader title="Kết nối broker" showBack />

          <section className="rounded-3xl border border-cyan-300/15 bg-transparent p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-100">
                  Kết nối broker
                </p>
                <h3 className="mt-2 font-display text-2xl font-semibold text-white">
                  Thêm tài khoản giao dịch
                </h3>
                <p className="mt-2 text-sm leading-6 text-cyber-muted">
                  Chọn đúng broker để kết nối đến hạ tầng giao dịch bot.
                </p>
              </div>
              <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-3 text-cyan-100">
                <Bot className="h-5 w-5" strokeWidth={1.9} />
              </div>
            </div>

            <form className="mt-5 space-y-4" onSubmit={handleConnect}>
              <div className="rounded-3xl border border-white/10 bg-transparent p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyber-text">
                      Chọn sàn giao dịch
                    </p>
                  </div>
                  <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                    {selectedBrokerPreset ? selectedBrokerPreset.name : "Chưa chọn"}
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-2">
                  {brokerPresets.map((preset) => {
                    const isActive = brokerNamesMatch(selectedBrokerPreset?.name, preset.name);

                    return (
                      <button
                        key={preset.name}
                        type="button"
                        onClick={() => {
                          setNotice(null);
                          setMt5BotTokenInput("");
                          setForm((current) => ({
                            ...current,
                            broker: preset.name,
                            server: getFixedMt5Server(preset.name) ??
                              (isServerPendingBroker(preset.name)
                              ? DBG_MARKETS_SERVER_PENDING_LABEL
                              : current.server === DBG_MARKETS_SERVER_PENDING_LABEL
                                ? ""
                                : current.server === DBG_MARKETS_FIXED_SERVER
                                ? ""
                                : current.server),
                          }));
                          if (preset.lane === "mt5") {
                            writeStoredMt5Broker(preset.name);
                          }
                        }}
                        className={`rounded-2xl border px-3 py-3 text-left transition ${
                          isActive
                            ? "border-cyan-300/35 bg-cyan-300/10 text-white"
                            : "border-white/10 bg-black/[0.08] text-cyber-muted"
                        }`}
                      >
                        <p className="text-sm font-semibold">{preset.name}</p>
                      </button>
                    );
                  })}
                </div>
              </div>

              <AnimatePresence initial={false} mode="wait">
                {!selectedBrokerPreset ? (
                  <motion.div
                    key="await-broker"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.18 }}
                    className="rounded-3xl border border-dashed border-white/10 bg-transparent px-4 py-5"
                  >
                    <p className="text-sm font-semibold text-white">
                      Chọn sàn để mở lane kết nối
                    </p>
                    <p className="mt-2 text-sm leading-6 text-cyber-muted">
                      Sau khi chọn broker, mini app sẽ tự mở đúng luồng kết nối: MT5 login form hoặc cTrader OAuth.
                    </p>
                  </motion.div>
                ) : selectedLane === "mt5" ? (
                  <motion.div
                    key="mt5-fields"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.22 }}
                    className="space-y-4"
                  >
                    <div className="grid grid-cols-2 rounded-3xl border border-white/10 bg-transparent p-1">
                      {(["connect", "control"] as const).map((tab) => {
                        const active = mt5WorkspaceTab === tab;
                        return (
                          <button
                            key={tab}
                            type="button"
                            onClick={() => setMt5WorkspaceTab(tab)}
                            className={`min-h-[44px] rounded-2xl px-3 py-2 text-sm font-semibold transition ${
                              active
                                ? "border border-cyan-300/30 bg-cyan-300/15 text-white"
                                : "border border-transparent text-cyber-muted hover:bg-black/[0.06] hover:text-white"
                            }`}
                          >
                            {tab === "connect" ? "Đăng nhập" : "Điều khiển bot"}
                          </button>
                        );
                      })}
                    </div>

	                    {mt5WorkspaceTab === "connect" ? (
	                      <>
	                        <div className="rounded-3xl border border-cyan-300/15 bg-transparent p-4">
	                          <div className="flex items-center gap-2 text-cyan-100">
	                            <Bot className="h-4 w-4" strokeWidth={1.9} />
	                            <p className="text-sm font-semibold uppercase tracking-[0.18em]">
	                              {mt5BotTokenRequired ? "Chọn bot và token" : "Chọn bot"}
	                            </p>
	                          </div>

                              {mt5BotCatalogLoadingEmpty ? (
                                <div className="mt-3 rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-4 text-sm text-cyan-100">
                                  <div className="flex items-center gap-2">
                                    <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                    Đang tải danh sách bot...
                                  </div>
                                </div>
                              ) : (
                                <div className="mt-3 grid gap-3">
                                  {mt5BotCatalogError ? (
                                    <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100">
                                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                        <span>
                                          {mt5Bots.length > 0
                                            ? "Chưa tải được bản mới của danh sách bot. Mini App đang giữ danh sách đã tải để không làm mất token."
                                            : "Chưa tải được danh sách bot. Token vẫn được khóa cho tới khi chọn được bot."}
                                        </span>
                                        <button
                                          type="button"
                                          onClick={() => void loadMt5BotCatalog()}
                                          disabled={loadingMt5Bots}
                                          className="inline-flex min-h-[38px] shrink-0 items-center justify-center gap-2 rounded-2xl border border-amber-200/25 bg-black/[0.08] px-3 py-2 text-xs font-semibold text-amber-50 transition hover:border-amber-200/40 hover:bg-black/[0.14] disabled:cursor-not-allowed disabled:opacity-60"
                                        >
                                          {loadingMt5Bots ? (
                                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                          ) : (
                                            <RefreshCcw className="h-4 w-4" strokeWidth={1.9} />
                                          )}
                                          Tải lại
                                        </button>
                                      </div>
                                    </div>
                                  ) : loadingMt5Bots ? (
                                    <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-3 text-sm text-cyan-100">
                                      <div className="flex items-center gap-2">
                                        <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                        Đang làm mới danh sách bot...
                                      </div>
                                    </div>
                                  ) : null}

                                  {mt5Bots.length > 0 ? (
                                    <label className="space-y-2">
                                      <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                                        Bot bạn muốn dùng
                                      </span>
                                      <button
                                        type="button"
                                        className={`${inputClassName} flex min-h-[54px] items-center gap-2 text-left font-semibold`}
                                        onClick={() => {
                                          const preferredBot = selectedMt5Bot ?? mt5Bots[0] ?? null;
                                          if (!preferredBot) return;
                                          setSelectedMt5BotName(preferredBot.bot_name);
                                        }}
                                      >
                                        <span>{selectedMt5Bot?.display_name || "Gs Algo"}</span>
                                      </button>
                                    </label>
                                  ) : (
                                    <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-4 text-sm text-cyber-muted">
                                      Chưa có bot nào khả dụng lúc này. Hãy thử làm mới lại sau ít phút.
                                    </div>
                                  )}

                                  {mt5BotTokenRequired ? (
                                    <label className="space-y-2">
                                      <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                                        Token bot
                                      </span>
                                      <input
                                        className={`${inputClassName} disabled:cursor-not-allowed disabled:opacity-70`}
                                        type="password"
                                        value={mt5BotTokenInput}
                                        onChange={(event) => setMt5BotTokenInput(event.target.value)}
                                        placeholder={selectedMt5Bot ? "Dán token bạn nhận được" : "Chọn bot trước rồi nhập token"}
                                        autoComplete="off"
                                        disabled={!selectedMt5Bot}
                                      />
                                    </label>
                                  ) : (
                                    <div className="rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm leading-6 text-emerald-100">
                                      User này đã được mở quyền Mini App, không cần nhập token.
                                    </div>
                                  )}
                                </div>
                              )}
		                        </div>

	                        <div className="rounded-3xl border border-cyan-300/15 bg-transparent p-4">
	                          <div className="flex items-center gap-2 text-cyan-100">
	                            <KeyRound className="h-4 w-4" strokeWidth={1.9} />
	                            <p className="text-sm font-semibold uppercase tracking-[0.18em]">
	                              Đăng nhập {resolvedBroker}
	                            </p>
	                          </div>
	                          <p className="mt-2 text-sm leading-6 text-cyber-muted">
	                            {mt5BotTokenRequired
	                              ? "Hoàn tất bot và token trước, sau đó nhập thông tin MT5 để lưu account."
	                              : "Chọn bot trước, sau đó nhập thông tin MT5 để lưu account."}
	                          </p>
	                        </div>

                            <div className="mx-auto grid w-full max-w-sm gap-4 rounded-3xl border border-white/10 bg-transparent p-4">
                              <label className="space-y-2">
                                <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                                  Server MT5
                                </span>
                                <input
                                  className={`${inputClassName} disabled:cursor-not-allowed disabled:opacity-70`}
                                  value={form.server}
                                  onChange={(event) =>
                                    setForm((current) => ({
                                      ...current,
                                      server: event.target.value,
                                    }))
                                  }
                                  placeholder={
                                    selectedBrokerFixedServer
                                      ? selectedBrokerFixedServer
                                      : selectedBrokerServerPending
                                      ? DBG_MARKETS_SERVER_PENDING_LABEL
                                      : "Ví dụ: Exness-MT5Real / XMGlobal-MT5 7"
                                  }
                                  disabled={
                                    Boolean(selectedBrokerFixedServer) ||
                                    selectedBrokerServerPending ||
                                    !mt5LoginPrerequisitesReady
                                  }
                                  autoComplete="off"
                                />
                                {selectedBrokerFixedServer && (
                                  <p className="text-xs leading-5 text-cyan-100">
                                    Server DBG được cố định: {selectedBrokerFixedServer}.
                                  </p>
                                )}
                                {selectedBrokerServerPending && (
                                  <p className="text-xs leading-5 text-amber-100">
                                    Server đang cập nhật.
                                  </p>
                                )}
                              </label>

                              <div className="grid gap-4 sm:grid-cols-2">
                                <label className="space-y-2">
                                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                                    Tài khoản MT5
                                  </span>
                                  <input
                                    className={`${inputClassName} disabled:cursor-not-allowed disabled:opacity-70`}
                                    value={form.login}
                                    onChange={(event) =>
                                      setForm((current) => ({
                                        ...current,
                                        login: event.target.value,
                                      }))
                                    }
                                    placeholder="Số tài khoản đăng nhập"
                                    inputMode="numeric"
                                    autoComplete="username"
                                    disabled={!mt5LoginPrerequisitesReady}
                                  />
                                </label>

                                <label className="space-y-2">
                                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                                    Mật khẩu MT5
                                  </span>
                                  <input
                                    className={`${inputClassName} disabled:cursor-not-allowed disabled:opacity-70`}
                                    type="password"
                                    value={form.password}
                                    onChange={(event) =>
                                      setForm((current) => ({
                                        ...current,
                                        password: event.target.value,
                                      }))
                                    }
                                    placeholder="Mật khẩu đăng nhập"
                                    autoComplete="current-password"
                                    disabled={!mt5LoginPrerequisitesReady}
                                  />
                                </label>
                              </div>

                              <div className="rounded-2xl border border-white/10 bg-transparent px-4 py-3">
                                <p className="text-[11px] uppercase tracking-[0.18em] text-cyber-muted">
                                  Broker sử dụng
                                </p>
                                <p className="mt-2 text-sm font-semibold text-white">
                                  {resolvedBroker}
                                </p>
                                <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                  Bạn đang đăng nhập theo đúng broker MT5 đã chọn ở bước trên.
                                </p>
                              </div>
                            </div>

                            <button
                              type="submit"
                              disabled={
                                submitting ||
                                selectedBrokerServerPending ||
                                loadingMt5Bots ||
                                !mt5LoginPrerequisitesReady
                              }
                              className="flex min-h-[52px] w-full items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/15 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/45 hover:bg-cyan-300/20 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {submitting ? (
                                <>
                                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                  {mt5VerificationPhase
                                    ? MT5_VERIFICATION_PHASE_LABEL[mt5VerificationPhase]
                                    : "Đang đăng nhập..."}
                                </>
                              ) : (
                                <>
                                  Đăng nhập
                                  <ChevronRight className="h-4 w-4" strokeWidth={1.9} />
	                                </>
	                              )}
	                            </button>
	                      </>
                    ) : (
                      <Mt5BotControlPanel
                        selectedBroker={resolvedBroker}
                        preferredBotName={selectedMt5BotName}
                        onSelectedBotChange={setSelectedMt5BotName}
                        mt5FullAccess={mt5FullAccess}
                        onRequireTerms={requireTerms}
                        termsEnabled={termsEnabled}
                      />
                    )}
                  </motion.div>
                ) : selectedBrokerIsIcMarkets ? (
                  <motion.div
                    key="icmarkets-api-lane"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.22 }}
                    className="rounded-3xl border border-cyan-300/20 bg-transparent p-5"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-100">
                          API Lane
                        </p>
                        <h4 className="mt-2 font-display text-2xl font-semibold text-white">
                          {IC_MARKETS_API_LANE_COPY.title}
                        </h4>
                      </div>
                      <span className="shrink-0 rounded-full border border-cyan-300/25 bg-cyan-300/10 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                        IC Markets
                      </span>
                    </div>

                    <p className="mt-4 text-base font-semibold leading-7 text-cyan-50">
                      {IC_MARKETS_API_LANE_COPY.lead}
                    </p>

                    <div className="mt-4 space-y-4 text-sm leading-7 text-cyber-muted">
                      {IC_MARKETS_API_LANE_COPY.paragraphs.map((paragraph) => (
                        <p key={paragraph}>{paragraph}</p>
                      ))}
                    </div>
                  </motion.div>
                ) : (
                  <motion.div
                    key="ctrader-lane"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.22 }}
                    className="space-y-4"
                  >
                    <div className="rounded-3xl border border-cyan-300/15 bg-transparent p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-cyan-100">
                          <Bot className="h-4 w-4" strokeWidth={1.9} />
                          <p className="text-sm font-semibold uppercase tracking-[0.18em]">1. Chọn bot</p>
                        </div>
                        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                          {selectedCTraderBot ? "Đã chọn" : "Bắt buộc"}
                        </span>
                      </div>

                      {loadingCTraderRuntime && ctraderBots.length === 0 ? (
                        <div className="mt-3 rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-4 text-sm text-cyan-100">
                          <div className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                            Đang tải bot...
                          </div>
                        </div>
                      ) : ctraderBots.length > 0 ? (
                        <select
                          value={selectedCTraderBot?.bot_code ?? ""}
                          onChange={(event) => setSelectedCTraderBotCode(event.target.value)}
                          className={`${inputClassName} mt-3`}
                        >
                          {ctraderBots.map((bot) => (
                            <option key={bot.bot_code} value={bot.bot_code}>
                              {bot.display_name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <div className="mt-3 rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-4 text-sm text-cyber-muted">
                          Chưa có bot khả dụng.
                        </div>
                      )}
                    </div>

                    <div className={`rounded-3xl border border-white/10 bg-transparent p-4 ${!selectedCTraderBot ? "opacity-60" : ""}`}>
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-cyan-100">
                          <KeyRound className="h-4 w-4" strokeWidth={1.9} />
                          <p className="text-sm font-semibold uppercase tracking-[0.18em]">
                            2. Kết nối {brokerDisplayName}
                          </p>
                        </div>
                        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                          {ctraderConnection ? "Đã kết nối" : "Chưa kết nối"}
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={handleConnectCTrader}
                        disabled={connectingCTrader || !selectedCTraderBot}
                        className="mt-4 flex min-h-[48px] w-full items-center justify-center gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-300/10 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/40 hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {connectingCTrader ? (
                          <>
                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                            Đang mở kết nối...
                          </>
                        ) : (
                          <>
                            {ctraderConnection ? "Kết nối lại" : "Kết nối"}
                            <ChevronRight className="h-4 w-4" strokeWidth={1.9} />
                          </>
                        )}
                      </button>
                    </div>

                    {!telegramTenantUserId && (
                      <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100">
                        Hãy mở mini app trong Telegram để kết nối đúng tài khoản.
                      </div>
                    )}

                    <div
                      className={`grid gap-4 rounded-3xl border border-white/10 bg-transparent p-4 ${
                        !loadingCTraderState && !ctraderConnection ? "hidden" : ""
                      }`}
                    >
                      {loadingCTraderState ? (
                        <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-4 text-sm text-cyan-100">
                          <div className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                            Đang tải dữ liệu {brokerDisplayName}...
                          </div>
                        </div>
                      ) : ctraderConnection ? (
                        <>
                          <div className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-4 py-3">
                            <div>
                              <p className="text-sm font-semibold text-white">{brokerDisplayName}</p>
                              <p className="mt-1 text-xs text-emerald-100">
                                {ctraderConnection.status || "active"} · {ctraderAccounts.length} account
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={handleSyncCTraderAccounts}
                              disabled={syncingCTraderAccounts}
                              className="flex min-h-[40px] items-center justify-center gap-2 rounded-2xl border border-emerald-300/20 bg-black/[0.08] px-3 py-2 text-xs font-semibold text-emerald-50 transition hover:border-emerald-300/35 hover:bg-black/[0.12] disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {syncingCTraderAccounts ? (
                                <>
                                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                  Đang tải
                                </>
                              ) : (
                                <>
                                  <RefreshCcw className="h-4 w-4" strokeWidth={1.9} />
                                  Sync
                                </>
                              )}
                            </button>
                          </div>

                          {ctraderAccounts.length > 0 ? (
                            <div
                              ref={ctraderAccountListRef}
                              className={`scroll-mt-24 space-y-3 rounded-2xl transition-all ${ctraderAccountListFocusClass}`}
                            >
                              <div className="flex items-center justify-between gap-3">
                                <div>
                                  <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-100">
                                    3. Chọn account
                                  </p>
                                </div>
                                <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                                  {ctraderAccounts.length} tài khoản
                                </span>
                              </div>

                              <div className="rounded-2xl border border-white/10 bg-transparent p-2">
                                <div className="grid grid-cols-3 gap-2">
                                  {([
                                    ["all", "Tất cả"],
                                    ["demo", "Demo"],
                                    ["live", "Live"],
                                  ] as const).map(([value, label]) => (
                                    <button
                                      key={value}
                                      type="button"
                                      onClick={() => setCTraderEnvironmentFilter(value)}
                                      className={`rounded-2xl px-3 py-3 text-sm font-semibold transition ${
                                        ctraderEnvironmentFilter === value
                                          ? "border border-cyan-300/30 bg-cyan-300/15 text-white"
                                          : "border border-transparent bg-black/[0.08] text-cyber-muted"
                                      }`}
                                    >
                                      {label}
                                    </button>
                                  ))}
                                </div>
                              </div>

                              <div className="space-y-2">
                                {filteredCTraderAccounts.map((account) => {
                                  const isSelected = account.id === selectedCTraderAccount?.id;
                                  const isLive = account.environment === "live";

                                  return (
                                    <button
                                      key={account.id}
                                      type="button"
                                      onClick={() => handleSelectCTraderAccount(account.id)}
                                      className={`w-full rounded-2xl border px-4 py-3 text-left text-sm transition ${
                                        isSelected
                                          ? "border-cyan-300/35 bg-cyan-300/10 text-white"
                                          : "border-white/10 bg-black/[0.08] text-cyber-muted"
                                      }`}
                                    >
                                      <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                          <p className="font-semibold text-white">
                                            {account.broker_name || "cTrader account"}
                                          </p>
                                          <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                            Login {account.account_number || "N/A"} · ID {account.external_account_id}
                                          </p>
                                          <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                            {account.base_currency || "N/A"} · {account.leverage || "N/A"}
                                          </p>
                                        </div>
                                        <div className="flex flex-col items-end gap-2">
                                          <span
                                            className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${
                                              isLive
                                                ? "border-amber-300/25 bg-amber-300/10 text-amber-100"
                                                : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                            }`}
                                          >
                                            {isLive ? "Live" : "Demo"}
                                          </span>
                                          {isSelected && (
                                            <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                                              Đang chọn
                                            </span>
                                          )}
                                          {persistedDefaultCTraderSelection?.trading_account_id === account.id && (
                                            <span className="rounded-full border border-emerald-300/20 bg-emerald-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-100">
                                              Đã lưu
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    </button>
                                  );
                                })}
                              </div>

                              {filteredCTraderAccounts.length === 0 && (
                                <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-5">
                                  <p className="text-sm font-semibold text-white">
                                    Không có tài khoản trong nhóm {ctraderEnvironmentFilter.toUpperCase()}
                                  </p>
                                </div>
                              )}

                              {selectedCTraderAccount && (
                                <div
                                  ref={ctraderSelectionRef}
                                  className={`scroll-mt-24 space-y-3 rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-4 transition-all ${ctraderSelectionFocusClass}`}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div>
                                      <p className="text-sm font-semibold text-white">Account dùng bot</p>
                                      <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                        Login {selectedCTraderAccount.account_number || "N/A"} · ID{" "}
                                        {selectedCTraderAccount.external_account_id}
                                      </p>
                                    </div>
                                    {selectedCTraderAccount.environment === "live" ? (
                                      <AlertTriangle className="h-5 w-5 text-amber-200" strokeWidth={1.9} />
                                    ) : (
                                      <CheckCircle2 className="h-5 w-5 text-emerald-200" strokeWidth={1.9} />
                                    )}
                                  </div>

                                  {selectedCTraderAccount.environment === "live" && (
                                    <label className="flex items-start gap-3 rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100">
                                      <input
                                        type="checkbox"
                                        checked={liveRiskConfirmed}
                                        onChange={(event) => setLiveRiskConfirmed(event.target.checked)}
                                        className="mt-1 h-4 w-4 rounded border-white/20 bg-black/[0.08]"
                                      />
                                      <span>Xác nhận tài khoản live.</span>
                                    </label>
                                  )}
                                </div>
                              )}

                              {selectedCTraderAccount && (
                                <div
                                  ref={ctraderControlRef}
                                  className={`scroll-mt-24 space-y-3 rounded-2xl border border-white/10 bg-transparent px-4 py-4 transition-all ${ctraderControlFocusClass}`}
                                >
                                  <div className="flex items-center justify-between gap-3">
                                    <div>
                                      <p className="text-sm font-semibold text-white">Bot</p>
                                      <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                        {selectedCTraderBot?.display_name || "Chưa chọn bot"}
                                      </p>
                                    </div>
                                    <span
                                      className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${
                                        activeCTraderDeployment
                                          ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                          : "border-white/10 bg-black/[0.08] text-cyber-muted"
                                      }`}
                                    >
                                      {activeCTraderDeployment ? "Đang bật" : "Đang tắt"}
                                    </span>
                                  </div>

                                  <div className="rounded-2xl border border-white/10 bg-transparent px-4 py-3">
                                    <p className="text-[11px] uppercase tracking-[0.16em] text-cyber-muted">
                                      Account
                                    </p>
                                    <p className="mt-2 text-sm font-semibold text-white">
                                      {selectedCTraderAccount.broker_name || brokerDisplayName} · Login{" "}
                                      {selectedCTraderAccount.account_number || "N/A"}
                                    </p>
                                    {activeCTraderDeployment?.last_transition_at && (
                                      <p className="mt-1 text-xs leading-5 text-cyber-muted">
                                        {formatCTraderDeploymentStatusLabel(activeCTraderDeployment.status)} ·{" "}
                                        {formatDateLabel(activeCTraderDeployment.last_transition_at)}
                                      </p>
                                    )}
                                  </div>

                                  {activeCTraderDeployment?.last_error && (
                                    <div className="rounded-2xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm leading-6 text-rose-100">
                                      {activeCTraderDeployment.last_error}
                                    </div>
                                  )}

                                  <div className="grid gap-2 sm:grid-cols-2">
                                    {activeCTraderDeployment ? (
                                      <button
                                        type="button"
                                        onClick={handleStopCTraderDeployment}
                                        disabled={stoppingCTraderDeployment}
                                        className="flex min-h-[48px] items-center justify-center gap-2 rounded-2xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm font-semibold text-rose-100 transition hover:border-rose-400/35 hover:bg-rose-400/15 disabled:cursor-not-allowed disabled:opacity-60"
                                      >
                                        {stoppingCTraderDeployment ? (
                                          <>
                                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                            Đang tắt
                                          </>
                                        ) : (
                                          "Tắt bot"
                                        )}
                                      </button>
                                    ) : (
                                      <button
                                        type="button"
                                        onClick={handleStartCTraderDeployment}
                                        disabled={startingCTraderDeployment || savingCTraderSelection || !ctraderStartReady}
                                        className="flex min-h-[48px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/35 hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-60"
                                      >
                                        {startingCTraderDeployment || savingCTraderSelection ? (
                                          <>
                                            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                            Đang bật
                                          </>
                                        ) : (
                                          "Bật bot"
                                        )}
                                      </button>
                                    )}

                                    <button
                                      type="button"
                                      onClick={handleRefreshCTraderRuntime}
                                      disabled={loadingCTraderRuntime}
                                      className="flex min-h-[48px] items-center justify-center gap-2 rounded-2xl border border-white/10 bg-black/[0.08] px-4 py-3 text-sm font-semibold text-white transition hover:border-white/20 hover:bg-black/[0.12] disabled:cursor-not-allowed disabled:opacity-60"
                                    >
                                      {loadingCTraderRuntime ? (
                                        <>
                                          <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                                          Đang tải
                                        </>
                                      ) : (
                                        <>
                                          <RefreshCcw className="h-4 w-4" strokeWidth={1.9} />
                                          Làm mới
                                        </>
                                      )}
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-5">
                              <p className="text-sm font-semibold text-white">Chưa thấy tài khoản nào</p>
                              <p className="mt-2 text-sm leading-6 text-cyber-muted">
                                Bấm Sync để lấy danh sách mới.
                              </p>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-5">
                          <p className="text-sm font-semibold text-white">Chưa kết nối {brokerDisplayName}</p>
                          <p className="mt-2 text-sm leading-6 text-cyber-muted">
                            Bấm Kết nối {brokerDisplayName} để đăng nhập và lấy tài khoản giao dịch của bạn.
                          </p>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {notice && (
                <div className={`rounded-2xl border px-4 py-3 text-sm leading-6 ${toneStyles[notice.tone]}`}>
                  {notice.message}
                </div>
              )}

            </form>
          </section>
        </div>
      </motion.main>

      <MiniappTermsModal
        open={termsModalOpen}
        version={termsVersion}
        accepting={termsAccepting}
        error={termsError}
        onAccept={acceptTerms}
      />
      <BottomNav />
    </>
  );
}
