/**
 * Real Backend API v2 client. Uses Telegram WebApp initData for auth.
 */

function normalizeBackendBase(raw: string): string {
  const s = raw.trim().replace(/\/$/, "");
  return s.replace(/\/api\/v2$/i, "");
}

function buildApiUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

const TELEGRAM_INIT_DATA_WAIT_MS = 2500;
const TELEGRAM_INIT_DATA_POLL_MS = 50;

function getBackendBaseUrl(): string {
  if (typeof window !== "undefined" && (window as unknown as { __BACKEND_URL?: string }).__BACKEND_URL) {
    return normalizeBackendBase((window as unknown as { __BACKEND_URL: string }).__BACKEND_URL);
  }
  const explicit = (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim();
  const fallbackApi = (process.env.NEXT_PUBLIC_API_URL || "").trim();
  const fromEnv = explicit || fallbackApi;
  if (fromEnv) {
    return normalizeBackendBase(fromEnv);
  }
  if (typeof window !== "undefined" && window.location?.origin) {
    return normalizeBackendBase(window.location.origin);
  }
  return "http://127.0.0.1:8001";
}

const getBaseUrl = (): string => getBackendBaseUrl();

/**
 * Get initData from Telegram WebApp (for use in Authorization header).
 * Returns empty string when not in Telegram context.
 */
function getInitData(): string {
  if (typeof window === "undefined") return "";
  const initData = (window as unknown as { Telegram?: { WebApp?: { initData?: string } } }).Telegram?.WebApp?.initData;
  return typeof initData === "string" ? initData : "";
}

async function waitForInitData(timeoutMs = TELEGRAM_INIT_DATA_WAIT_MS): Promise<string> {
  const readyInitData = getInitData();
  if (readyInitData || typeof window === "undefined" || timeoutMs <= 0) {
    return readyInitData;
  }

  const startedAt = Date.now();

  return new Promise((resolve) => {
    let timer: number | null = null;

    const done = (value: string) => {
      if (timer != null) {
        window.clearTimeout(timer);
      }
      resolve(value);
    };

    const poll = () => {
      const initData = getInitData();
      if (initData) {
        done(initData);
        return;
      }

      if (Date.now() - startedAt >= timeoutMs) {
        done("");
        return;
      }

      timer = window.setTimeout(poll, TELEGRAM_INIT_DATA_POLL_MS);
    };

    poll();
  });
}

export interface FetchFromAPIOptions extends RequestInit {
  /** Override initData (default: from window.Telegram.WebApp.initData) */
  initData?: string;
  /** Protected endpoints wait briefly for Telegram initData by default. */
  authMode?: "required" | "optional" | "none";
  authWaitMs?: number;
}

export interface BackendErrorInfo {
  public_code?: string;
  message_vi?: string;
  message_en?: string;
  action?: string;
  retryable?: boolean;
  group?: string;
}

export class BackendAPIError extends Error {
  status: number;
  code: string;
  errorInfo?: BackendErrorInfo;

  constructor(
    message: string,
    options: { status: number; code: string; errorInfo?: BackendErrorInfo }
  ) {
    super(message);
    this.name = "BackendAPIError";
    this.status = options.status;
    this.code = options.code;
    this.errorInfo = options.errorInfo;
  }
}

const friendlyErrorMessages: Record<string, string> = {
  account_quota_exceeded:
    "Bạn đã đạt giới hạn số tài khoản MT5 của gói hiện tại. Hãy xóa tài khoản cũ hoặc liên hệ hỗ trợ để mở thêm slot.",
  quota_exceeded:
    "Bạn đã đạt giới hạn số bot đang chạy của gói hiện tại. Hãy tắt bot đang chạy hoặc nâng cấp gói.",
  telegram_user_has_active_bot:
    "Mỗi Telegram ID chỉ được dùng 1 bot tại một thời điểm. Hãy tắt bot hiện tại trước khi bật bot khác.",
  bot_control_cooldown_active:
    "Bạn vừa bật/tắt bot. Vui lòng chờ đủ 60 giây rồi thao tác lại.",
  rate_limited: "Bạn thao tác hơi nhanh. Vui lòng chờ một chút rồi thử lại.",
  bot_token_required: "Bạn cần nhập token để mở quyền cho bot này.",
  bot_token_not_found: "Token không đúng hoặc không tồn tại.",
  bot_token_already_used: "Token này đã được sử dụng.",
  bot_token_expired: "Token đã hết hạn.",
  bot_token_revoked: "Token đã bị khóa.",
  bot_token_wrong_bot: "Token này không dùng cho bot đã chọn.",
  bot_token_partner_locked: "Đối tác cấp token đang bị khóa. Vui lòng liên hệ hỗ trợ.",
  bot_token_entitlement_expired: "Quyền dùng bot đã hết hạn. Vui lòng nhập token mới.",
  bot_token_entitlement_not_found: "Bạn chưa mở quyền token cho bot này.",
  start_transition_in_progress:
    "Account đang có thao tác bật/tắt bot chưa đồng bộ xong. Vui lòng làm mới rồi thử lại.",
  mt5_runtime_maintenance: "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.",
  windows_runtime_unhealthy: "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.",
  runner_queue_backlog: "Máy chạy MT5 đang xử lý nhiều tác vụ. Vui lòng thử lại sau ít phút.",
  runner_offline: "Máy chạy MT5 đang tạm mất kết nối. Vui lòng thử lại sau ít phút.",
  runner_full: "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
  slot_not_ipc_ready: "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.",
  slot_resident_worker_missing: "Máy chạy MT5 đang khởi động lại phiên bot. Vui lòng thử lại sau ít phút.",
  no_available_unreserved_slot: "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
  no_scheduler_candidate: "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
  no_healthy_slot_available: "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
  no_available_healthy_slot: "Server đang có quá nhiều người truy cập. Vui lòng thử lại sau.",
  TERMS_NOT_ACCEPTED:
    "Vui lòng đọc và xác nhận Điều khoản sử dụng & Cảnh báo rủi ro trước khi tiếp tục.",
  terms_not_accepted:
    "Vui lòng đọc và xác nhận Điều khoản sử dụng & Cảnh báo rủi ro trước khi tiếp tục.",
  invalid_terms_version: "Phiên bản điều khoản đã thay đổi. Vui lòng tải lại Mini App và xác nhận lại.",
  terms_checkboxes_required: "Vui lòng xác nhận đầy đủ các nội dung bắt buộc trước khi tiếp tục.",
};

export function getBackendErrorCode(error: unknown): string | null {
  if (error instanceof BackendAPIError) {
    return error.code || null;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function normalizeErrorText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (value != null) return JSON.stringify(value);
  return "";
}

function parseBackendErrorResponse(rawText: string): {
  detail: string;
  code: string;
  errorInfo?: BackendErrorInfo;
} {
  try {
    const payload = asRecord(JSON.parse(rawText));
    const detailPayload = asRecord(payload.detail);
    const info = asRecord(payload.error_info);
    const code = normalizeErrorText(
      info.public_code ?? payload.error ?? detailPayload.error ?? payload.detail
    );
    const errorInfo = Object.keys(info).length > 0 ? (info as BackendErrorInfo) : undefined;
    const detail =
      friendlyErrorMessages[code] ||
      normalizeErrorText(info.message_vi) ||
      normalizeErrorText(info.message_en) ||
      normalizeErrorText(payload.message) ||
      normalizeErrorText(detailPayload.message) ||
      normalizeErrorText(payload.detail) ||
      rawText;
    return {
      detail: detail || rawText,
      code,
      errorInfo,
    };
  } catch {
    return {
      detail: rawText,
      code: "",
      errorInfo: undefined,
    };
  }
}

/**
 * Fetch from Backend API v2. Injects Authorization: tma <initData> from Telegram WebApp.
 * Use for wallet, rewards, and other active v2 endpoints.
 */
export async function fetchFromAPI<T = unknown>(
  path: string,
  options: FetchFromAPIOptions = {}
): Promise<T> {
  const {
    initData: customInitData,
    authMode = "required",
    authWaitMs = TELEGRAM_INIT_DATA_WAIT_MS,
    headers = {},
    ...rest
  } = options;
  const initData =
    customInitData ??
    (authMode === "required"
      ? await waitForInitData(authWaitMs)
      : authMode === "optional"
        ? getInitData()
        : "");
  const url = buildApiUrl(getBaseUrl(), path);

  const authHeaders: Record<string, string> = {};
  if (initData) {
    authHeaders.Authorization = `tma ${initData}`;
  }

  const res = await fetch(url, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...headers,
    },
  });

  if (!res.ok) {
    const text = await res.text();
    const parsed = parseBackendErrorResponse(text);
    const detail = parsed.detail || text || res.statusText;
    const code = parsed.code;
    const errorInfo = parsed.errorInfo;
    throw new BackendAPIError(detail || `API ${res.status}`, {
      status: res.status,
      code: code || detail || `http_${res.status}`,
      errorInfo,
    });
  }

  const contentType = res.headers.get("content-type");
  if (contentType?.includes("application/json")) {
    return res.json() as Promise<T>;
  }
  return res.text() as Promise<T>;
}

/** GET /api/v2/wallet/info response */
export interface WalletInfoResponse {
  balance: number;
  equity: number;
  deposit_address: string;
  currency: string;
}

/** GET /api/v2/wallet/transactions response */
export interface WalletTransactionItem {
  id: number | string;
  type: string;
  amount: number;
  status: string;
  created_at: string;
  tx_ref?: string;
}

export interface WalletTransactionsResponse {
  transactions: WalletTransactionItem[];
}

/** POST /api/v2/wallet/withdraw body */
export interface WithdrawRequest {
  amount: number;
  wallet_address: string;
}

export function fetchWalletInfo(): Promise<WalletInfoResponse> {
  return fetchFromAPI<WalletInfoResponse>("/api/v2/wallet/info");
}

export interface MT5AccountItem {
  id: number;
  broker: string;
  server: string;
  login: string;
  status: string;
  label?: string | null;
  is_active?: boolean;
  has_credentials?: boolean;
  last_error?: string | null;
  verified_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  verification_job_id?: number | null;
  verification_job_status?: string | null;
  verification_state?: string | null;
  verification_ui_state?: string | null;
  verification_requested_at?: string | null;
  verification_completed_at?: string | null;
  active_deployment_id?: number | null;
  active_deployment_status?: string | null;
  runner_id?: string | null;
  slot_id?: string | null;
}

export interface MT5AccountsResponse {
  items: MT5AccountItem[];
}

export interface MT5DashboardResponse {
  accounts?: MT5AccountItem[];
  deployments?: MT5DeploymentItem[];
}

export interface MT5DeploymentItem {
  id: number;
  account_id: number;
  bot_code: string;
  bot_name: string;
  profile_class: string;
  status: string;
  desired_state: string;
  runner_id?: string | null;
  slot_id?: string | null;
  health_status?: string | null;
  last_error?: string | null;
  last_heartbeat_at?: string | null;
  config_json?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  broker: string;
  server: string;
  login: string;
}

export interface MT5DeploymentsResponse {
  items: MT5DeploymentItem[];
}

export interface ConnectMt5AccountRequest {
  broker: string;
  server: string;
  login: string;
  password: string;
  label?: string;
}

export interface ConnectMt5AccountResponse {
  account_id: number;
  status: string;
  account: MT5AccountItem;
}

export interface DeleteMt5AccountResponse {
  account_id: number;
  deleted: boolean;
  status: string;
  verification_cancelled_total?: number;
  slot_released?: boolean;
}

export interface StartMt5DeploymentRequest {
  account_id: number;
  bot_name: string;
  bot_config_overrides?: Record<string, unknown>;
  entitlement_id?: string;
}

export interface StartMt5DeploymentResponse {
  deployment_id?: number;
  runner_id?: string | null;
  slot_id?: string | null;
  status?: string;
  deployment?: Record<string, unknown>;
  command?: Record<string, unknown>;
  bot?: Record<string, unknown>;
  scheduler?: Record<string, unknown>;
}

export interface StopMt5DeploymentRequest {
  deployment_id: number;
  reason?: string;
}

export interface StopMt5DeploymentResponse {
  status?: string;
  deployment?: Record<string, unknown>;
  command?: Record<string, unknown>;
}

export interface MT5BotCatalogItem {
  bot_id: string;
  bot_name: string;
  display_name: string;
  language: string;
  version: string;
  profile_class: string;
  runtime_entry: string;
  required_params: string[];
  risk_profile: Record<string, unknown>;
  indicator_requirements: string[];
  strategy_tags: string[];
  resource_hints: Record<string, unknown>;
  supports_demo: boolean;
  supports_live: boolean;
  default_config_path?: string | null;
  runtime_env: Record<string, unknown>;
  checksum: string;
  source_path: string;
}

export interface MT5BotCatalogResponse {
  items: MT5BotCatalogItem[];
}

export interface MT5BotTokenEntitlement {
  entitlement_id: string;
  token_id?: string;
  partner_id?: string;
  telegram_id?: string;
  user_id?: number | null;
  account_id?: number | null;
  deployment_id?: number | null;
  bot_code: string;
  status: string;
  starts_at?: string | null;
  expires_at?: string | null;
}

export interface MT5BotTokenEntitlementsResponse {
  items: MT5BotTokenEntitlement[];
}

export interface ClaimMt5BotTokenRequest {
  account_id: number;
  bot_name: string;
  token: string;
}

export interface ClaimMt5BotTokenResponse {
  entitlement: MT5BotTokenEntitlement;
}

export interface MiniappAccessResponse {
  mt5_full_access: boolean;
  bot_token_required: boolean;
  terms_enforcement_enabled?: boolean;
}

export interface MiniappTermsStatusResponse {
  accepted: boolean;
  version: string;
  accepted_at?: string | null;
  requires_acceptance: boolean;
  enabled?: boolean;
}

export interface AcceptMiniappTermsRequest {
  version: string;
  checkbox_1: boolean;
  checkbox_2: boolean;
  checkbox_3: boolean;
  partner_id?: string;
  token_id?: string;
}

interface MiniBotCatalogItem {
  bot_code: string;
  bot_id?: string;
  bot_name: string;
  display_name?: string;
  language?: string;
  version?: string;
  profile_class: string;
  label?: string;
  runtime_entry?: string;
  required_params?: string[];
  risk_profile?: Record<string, unknown>;
  indicator_requirements?: string[];
  strategy_tags?: string[];
  resource_hints?: Record<string, unknown>;
  supports_demo?: boolean;
  supports_live?: boolean;
  default_config_path?: string | null;
  runtime_env?: Record<string, unknown>;
  checksum?: string;
  source_path?: string;
}

const GSALGO_DISPLAY_NAME = "Gs Algo";
const GSALGO_DISPLAY_IDENTITIES = new Set(["gsalgo", "gsalgomt5bot"]);

function normalizeBotDisplayIdentity(value?: string | null): string {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

function formatMt5BotDisplayName(bot: MiniBotCatalogItem): string {
  const identities = [
    bot.bot_id,
    bot.bot_code,
    bot.bot_name,
    bot.display_name,
    bot.label,
  ].map(normalizeBotDisplayIdentity);

  if (identities.some((identity) => GSALGO_DISPLAY_IDENTITIES.has(identity))) {
    return GSALGO_DISPLAY_NAME;
  }

  return bot.display_name || bot.bot_name || bot.label || bot.bot_code;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

export interface CTraderAuthorizeUrlRequest {
  tenant_user_id: string;
  redirect_uri: string;
  state?: string;
  scope?: string;
}

export interface CTraderAuthorizeUrlResponse {
  auth_url: string;
  state: string;
}

export interface CTraderBrokerConnectionsResponse {
  items: CTraderBrokerConnection[];
}

export interface CTraderBrokerConnection {
  id: string;
  tenant_user_id: string;
  provider: string;
  external_user_id?: string | null;
  token_type?: string | null;
  scope?: string | null;
  expires_at?: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
}

export interface CTraderOAuthCallbackResponse {
  success: boolean;
  tenant_user_id: string;
  redirect_uri: string;
  scope?: string | null;
  client_state?: string | null;
  state_validated: boolean;
  connection: CTraderBrokerConnection;
}

interface CTraderOAuthCallbackResponseWithAccounts extends CTraderOAuthCallbackResponse {
  accounts: CTraderTradingAccount[];
  discover_error?: string | null;
  default_account_selection?: Record<string, unknown> | null;
  next_action?: "select_account" | "confirm_live_risk" | "ready" | string | null;
}

export interface CTraderPublicOAuthCallbackResponse extends CTraderOAuthCallbackResponseWithAccounts {}
export interface CTraderMiniappOAuthCallbackResponse extends CTraderOAuthCallbackResponseWithAccounts {}

export interface DiscoverCTraderAccountsRequest {
  tenant_user_id: string;
  broker_connection_id: string;
}

export interface SelectDefaultCTraderAccountRequest {
  broker_connection_id: string;
  trading_account_id: string;
  live_risk_confirmed?: boolean;
}

export interface CTraderTradingAccount {
  id: string;
  broker_connection_id: string;
  tenant_user_id: string;
  provider: string;
  external_account_id: string;
  environment: "live" | "demo";
  broker_name?: string | null;
  account_number?: string | null;
  base_currency?: string | null;
  leverage?: string | null;
  status: string;
  raw_payload: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
}

export interface CTraderTradingAccountsResponse {
  items: CTraderTradingAccount[];
}

export interface CTraderBotCatalogItem {
  bot_code: string;
  display_name: string;
  description?: string | null;
  runtime_family?: string | null;
  port_mode?: string | null;
  aliases: string[];
  source_bots: string[];
  default_config: Record<string, unknown>;
  source_path: string;
  is_template: boolean;
  execution_supported: boolean;
}

export interface CTraderBotCatalogResponse {
  items: CTraderBotCatalogItem[];
  execution_ready: boolean;
  blockers: string[];
}

export interface CTraderBetaOverview {
  provider: string;
  surface: string;
  availability_status: string;
  availability_label_vi: string;
  description_vi: string;
  execution_mode: string;
  execution_mode_label_vi: string;
  capabilities: string[];
  visible_bots: number;
  execution_ready: boolean;
  blockers: string[];
  updated_at: number;
  session_pool: {
    status: string;
    running: boolean;
    account_sessions: Record<string, unknown>;
    error?: string | null;
  };
  deployment_reconciler: {
    health_status: string;
    running: boolean;
    coordinator_status: string;
    last_success_at?: string | null;
    last_failure_at?: string | null;
    last_error?: string | null;
    last_result: Record<string, number>;
  };
}

export interface CTraderDeployment {
  id: string;
  broker_connection_id: string;
  trading_account_id: string;
  tenant_user_id: string;
  provider: string;
  bot_code: string;
  bot_display_name?: string | null;
  environment: "demo" | "live" | string;
  desired_state: string;
  status: string;
  config_json: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
  last_error?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  last_transition_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface CTraderDeploymentEvent {
  id: string;
  deployment_id: string;
  trading_account_id: string;
  tenant_user_id: string;
  provider: string;
  event_type: string;
  event_status?: string | null;
  trace_id?: string | null;
  payload_json: Record<string, unknown>;
  occurred_at?: string | null;
  created_at: string;
}

export interface CTraderDeploymentsResponse {
  items: CTraderDeployment[];
}

export interface CTraderDeploymentEventsResponse {
  items: CTraderDeploymentEvent[];
}

export interface CTraderRuntimeStateResponse {
  overview: CTraderBetaOverview;
  bot_catalog: CTraderBotCatalogResponse;
  deployments: CTraderDeploymentsResponse;
  active_deployment?: CTraderDeployment | null;
  deployment_detail?: CTraderDeployment | null;
  deployment_events: CTraderDeploymentEventsResponse;
}

export interface EvaluateCTraderDeploymentRequest {
  market?: Record<string, unknown>;
}

export interface EvaluateCTraderDeploymentResponse {
  deployment: CTraderDeployment;
  evaluation: {
    bot_code: string;
    display_name?: string | null;
    source_path?: string | null;
    config: Record<string, unknown>;
    result: Record<string, unknown>;
    execution_supported: boolean;
  };
  event: CTraderDeploymentEvent;
}

export interface StartCTraderDeploymentRequest {
  broker_connection_id: string;
  trading_account_id: string;
  bot_code: string;
  config?: Record<string, unknown>;
  live_risk_confirmed?: boolean;
  force_reconnect?: boolean;
  reason?: string;
}

export interface StartCTraderDeploymentResponse {
  deployment: CTraderDeployment;
  session?: Record<string, unknown> | null;
  start_status: string;
  deduped: boolean;
}

export interface StopCTraderDeploymentRequest {
  reason?: string;
}

export function fetchMt5Accounts(): Promise<MT5AccountsResponse> {
  return fetchFromAPI<MT5AccountsResponse>("/api/v2/miniapp/accounts");
}

export function fetchMt5Deployments(): Promise<MT5DeploymentsResponse> {
  return fetchFromAPI<MT5DeploymentsResponse>("/api/v2/miniapp/deployments");
}

export function fetchMt5Dashboard(): Promise<MT5DashboardResponse> {
  return fetchFromAPI<MT5DashboardResponse>("/api/v2/miniapp/dashboard");
}

export function connectMt5Account(body: ConnectMt5AccountRequest): Promise<ConnectMt5AccountResponse> {
  return fetchFromAPI<ConnectMt5AccountResponse>("/api/v2/accounts/connect", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteMt5Account(accountId: number): Promise<DeleteMt5AccountResponse> {
  return fetchFromAPI<DeleteMt5AccountResponse>(`/api/v2/accounts/${accountId}`, {
    method: "DELETE",
    body: JSON.stringify({ reason: "miniapp_user_delete" }),
  });
}

export function fetchMt5BotCatalog(forceSync = false): Promise<MT5BotCatalogResponse> {
  const query = forceSync ? "?force_sync=true" : "";
  return fetchFromAPI<MiniBotCatalogItem[]>(`/api/v2/mini/bots${query}`).then((items) => ({
    items: items.map((bot) => ({
      bot_id: bot.bot_id || bot.bot_code,
      bot_name: bot.bot_name,
      display_name: formatMt5BotDisplayName(bot),
      language: bot.language || "python",
      version: bot.version || "",
      profile_class: bot.profile_class,
      runtime_entry: bot.runtime_entry || "",
      required_params: stringArray(bot.required_params),
      risk_profile: asRecord(bot.risk_profile),
      indicator_requirements: stringArray(bot.indicator_requirements),
      strategy_tags: stringArray(bot.strategy_tags),
      resource_hints: asRecord(bot.resource_hints),
      supports_demo: bot.supports_demo ?? true,
      supports_live: bot.supports_live ?? true,
      default_config_path: bot.default_config_path ?? null,
      runtime_env: asRecord(bot.runtime_env),
      checksum: bot.checksum || "",
      source_path: bot.source_path || "",
    })),
  }));
}

export function fetchMt5BotTokenEntitlements(accountId: number): Promise<MT5BotTokenEntitlementsResponse> {
  return fetchFromAPI<MT5BotTokenEntitlementsResponse>(
    `/api/v2/miniapp/bot-token/entitlements?account_id=${encodeURIComponent(String(accountId))}`
  );
}

export function claimMt5BotToken(body: ClaimMt5BotTokenRequest): Promise<ClaimMt5BotTokenResponse> {
  return fetchFromAPI<ClaimMt5BotTokenResponse>("/api/v2/miniapp/bot-token/claim", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchMiniappAccess(): Promise<MiniappAccessResponse> {
  return fetchFromAPI<MiniappAccessResponse>("/api/v2/miniapp/access");
}

export function fetchMiniappTermsStatus(): Promise<MiniappTermsStatusResponse> {
  return fetchFromAPI<MiniappTermsStatusResponse>("/api/v2/miniapp/terms/status");
}

export function acceptMiniappTerms(body: AcceptMiniappTermsRequest): Promise<MiniappTermsStatusResponse> {
  return fetchFromAPI<MiniappTermsStatusResponse>("/api/v2/miniapp/terms/accept", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function startMt5Deployment(
  body: StartMt5DeploymentRequest
): Promise<StartMt5DeploymentResponse> {
  return fetchFromAPI<StartMt5DeploymentResponse>("/api/v2/deployments/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function stopMt5Deployment(
  body: StopMt5DeploymentRequest
): Promise<StopMt5DeploymentResponse> {
  return fetchFromAPI<StopMt5DeploymentResponse>("/api/v2/deployments/stop", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createCTraderAuthorizeUrl(
  body: CTraderAuthorizeUrlRequest
): Promise<CTraderAuthorizeUrlResponse> {
  return fetchFromAPI<CTraderAuthorizeUrlResponse>("/api/v2/miniapp/ctrader/authorize-url", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface CompleteCTraderOAuthCallbackParams {
  code: string;
  state?: string | null;
  error?: string | null;
  error_description?: string | null;
  tenant_user_id?: string | null;
  scope?: string | null;
}

function buildCTraderCallbackRedirectUri(): string {
  if (typeof window !== "undefined") {
    return new URL("/bot/ctrader/callback", window.location.origin).toString();
  }
  return "/bot/ctrader/callback";
}

function completeCTraderOAuthCallback<TResponse>(
  endpoint: string,
  params: CompleteCTraderOAuthCallbackParams,
  authMode: FetchFromAPIOptions["authMode"]
): Promise<TResponse> {
  return fetchFromAPI<TResponse>(endpoint, {
    authMode,
    method: "POST",
    body: JSON.stringify({
      code: params.code,
      state: params.state,
      scope: params.scope,
      redirect_uri: buildCTraderCallbackRedirectUri(),
    }),
  });
}

export function completeCTraderMiniappOAuthCallback(
  params: CompleteCTraderOAuthCallbackParams
): Promise<CTraderMiniappOAuthCallbackResponse> {
  return completeCTraderOAuthCallback<CTraderMiniappOAuthCallbackResponse>(
    "/api/v2/miniapp/ctrader/callback/complete",
    params,
    "required"
  );
}

export function completeCTraderPublicOAuthCallback(
  params: CompleteCTraderOAuthCallbackParams
): Promise<CTraderPublicOAuthCallbackResponse> {
  return completeCTraderOAuthCallback<CTraderPublicOAuthCallbackResponse>(
    "/api/v2/public/ctrader/callback/complete",
    params,
    "none"
  );
}

export function fetchCTraderConnections(
  _tenantUserId: string
): Promise<CTraderBrokerConnectionsResponse> {
  return fetchFromAPI<CTraderBrokerConnectionsResponse>("/api/v2/miniapp/ctrader/connections");
}

export function refreshCTraderConnection(
  connectionId: string,
  _tenantUserId: string
): Promise<CTraderBrokerConnection> {
  return fetchFromAPI<CTraderBrokerConnection>(`/api/v2/miniapp/ctrader/connections/${connectionId}/refresh`, {
    method: "POST",
  });
}

export function discoverCTraderAccounts(
  body: DiscoverCTraderAccountsRequest
): Promise<CTraderTradingAccountsResponse> {
  return fetchFromAPI<CTraderTradingAccountsResponse>("/api/v2/miniapp/ctrader/accounts/discover", {
    method: "POST",
    body: JSON.stringify({
      broker_connection_id: body.broker_connection_id,
    }),
  });
}

export function selectDefaultCTraderAccount(
  body: SelectDefaultCTraderAccountRequest
): Promise<CTraderBrokerConnection> {
  return fetchFromAPI<CTraderBrokerConnection>("/api/v2/miniapp/ctrader/accounts/select-default", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchCTraderAccounts(
  _tenantUserId: string
): Promise<CTraderTradingAccountsResponse> {
  return fetchFromAPI<CTraderTradingAccountsResponse>("/api/v2/miniapp/ctrader/accounts");
}

export function fetchCTraderRuntimeState(
  tradingAccountId?: string,
  eventsLimit = 8
): Promise<CTraderRuntimeStateResponse> {
  const query = new URLSearchParams();
  if (tradingAccountId) {
    query.set("trading_account_id", tradingAccountId);
  }
  query.set("events_limit", String(Math.max(1, Math.min(eventsLimit, 100))));
  return fetchFromAPI<CTraderRuntimeStateResponse>(`/api/v2/miniapp/ctrader/runtime-state?${query.toString()}`);
}

export function startCTraderDeployment(
  body: StartCTraderDeploymentRequest
): Promise<StartCTraderDeploymentResponse> {
  return fetchFromAPI<StartCTraderDeploymentResponse>("/api/v2/miniapp/ctrader/deployments/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function evaluateCTraderDeployment(
  deploymentId: string,
  body: EvaluateCTraderDeploymentRequest = {}
): Promise<EvaluateCTraderDeploymentResponse> {
  return fetchFromAPI<EvaluateCTraderDeploymentResponse>(
    `/api/v2/miniapp/ctrader/deployments/${deploymentId}/evaluate`,
    {
      method: "POST",
      body: JSON.stringify({
        market: body.market ?? {},
      }),
    }
  );
}

export function stopCTraderDeployment(
  deploymentId: string,
  body: StopCTraderDeploymentRequest = {}
): Promise<CTraderDeployment> {
  return fetchFromAPI<CTraderDeployment>(`/api/v2/miniapp/ctrader/deployments/${deploymentId}/stop`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchTransactions(limit?: number): Promise<WalletTransactionsResponse> {
  const q = limit != null ? `?limit=${limit}` : "";
  return fetchFromAPI<WalletTransactionsResponse>(`/api/v2/wallet/transactions${q}`);
}

export function requestWithdrawal(body: WithdrawRequest): Promise<{ success: boolean; withdrawal: unknown }> {
  return fetchFromAPI("/api/v2/wallet/withdraw", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** GET /api/v2/rewards/info response */
export interface RewardsInfoResponse {
  referral_link: string;
  total_referrals: number;
  total_bonus: number;
}

/** GET /api/v2/rewards/leaderboard response */
export interface LeaderboardEntry {
  rank: number;
  referral_count: number;
  masked_username: string;
}

export interface LeaderboardResponse {
  leaderboard: LeaderboardEntry[];
}

/** GET /api/v2/rewards/bonus-history response */
export interface BonusEventItem {
  id: number;
  amount: number;
  reason: string;
  created_at: string;
}

export interface BonusHistoryResponse {
  events: BonusEventItem[];
}

export function fetchRewardsInfo(): Promise<RewardsInfoResponse> {
  return fetchFromAPI<RewardsInfoResponse>("/api/v2/rewards/info");
}

export function fetchLeaderboard(): Promise<LeaderboardResponse> {
  return fetchFromAPI<LeaderboardResponse>("/api/v2/rewards/leaderboard");
}

export function fetchBonusHistory(limit?: number): Promise<BonusHistoryResponse> {
  const q = limit != null ? `?limit=${limit}` : "";
  return fetchFromAPI<BonusHistoryResponse>(`/api/v2/rewards/bonus-history${q}`);
}
