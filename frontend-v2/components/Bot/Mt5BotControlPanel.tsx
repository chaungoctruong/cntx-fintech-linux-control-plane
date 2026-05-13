"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bot, Loader2, PauseCircle, Pencil, PlayCircle, RefreshCcw, ServerCog, Trash2 } from "lucide-react";

import {
  type MT5AccountItem,
} from "@/lib/api";
import { MINIAPP_RISK_WARNING_SHORT } from "@/components/Bot/MiniappTermsModal";
import {
  LOT_SIZE_DEFAULT,
  formatTokenExpiry,
  getDeploymentLotSize,
  getAccountStatusPillClassName,
  getDeploymentStatusPillClassName,
  humanizeAccountStatus,
  humanizeDeploymentProgress,
  humanizeDeploymentStatus,
  isMt5AccountReady,
  isTransitionalDeploymentStatus,
} from "@/components/Bot/mt5ControlUtils";
import { useMt5BotControl } from "@/components/Bot/useMt5BotControl";
import { useMt5BotActions } from "@/components/Bot/useMt5BotActions";
import { useMt5BotDerivedState } from "@/components/Bot/useMt5BotDerivedState";

type NoticeTone = "success" | "error" | "info";

type Notice = {
  tone: NoticeTone;
  message: string;
};

const toneStyles = {
  success: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100",
  error: "border-rose-400/25 bg-rose-400/10 text-rose-100",
  info: "border-cyan-300/25 bg-cyan-300/10 text-cyan-100",
} as const;

const selectClassName =
  "w-full rounded-2xl border border-white/10 bg-transparent px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-300/40 focus:bg-black/[0.06]";
const statusPillClassName =
  "shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em]";
const CONTROL_PLANE_REFRESH_MS = 15000;
// While a deployment is in transitional state (start_requested / starting /
// stop_requested / queued) poll faster so the UI catches the moment Windows
// runner flips to running/stopped instead of waiting up to 15s. 3s matches
// the runner heartbeat cadence without becoming spammy at scale.
const CONTROL_PLANE_TRANSITION_REFRESH_MS = 3000;
type Mt5BotControlPanelProps = {
  selectedBroker: string;
  preferredBotName?: string;
  onSelectedBotChange?: (botName: string) => void;
  mt5FullAccess?: boolean;
  onRequireTerms?: (afterAccept?: () => void) => boolean;
  termsEnabled?: boolean;
};

export default function Mt5BotControlPanel({
  selectedBroker,
  preferredBotName,
  onSelectedBotChange,
  mt5FullAccess = false,
  onRequireTerms,
  termsEnabled = false,
}: Mt5BotControlPanelProps) {
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [selectedBotName, setSelectedBotName] = useState("");
  const [botTokenInput, setBotTokenInput] = useState("");
  const [lotSizeInput, setLotSizeInput] = useState(LOT_SIZE_DEFAULT);
  const [lotEditorOpen, setLotEditorOpen] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const pushNotice = useCallback((tone: NoticeTone, message: string) => {
    setNotice({ tone, message });
  }, []);
  const pushControlError = useCallback((message: string) => {
    pushNotice("error", message);
  }, [pushNotice]);
  const clearNotice = useCallback(() => {
    setNotice(null);
  }, []);
  const {
    accounts,
    deployments,
    bots,
    botCatalogError,
    loadingState,
    refreshingState,
    botTokenEntitlements,
    setBotTokenEntitlements,
    loadState,
    loadBotTokenEntitlements,
    refreshState,
  } = useMt5BotControl({
    selectedBroker,
    selectedAccountId,
    onError: pushControlError,
  });
  const {
    startingBot,
    stoppingBot,
    deletingAccount,
    unlockingBotToken,
    deleteConfirmAccountId,
    setDeleteConfirmAccountId,
    handleUnlockBotToken,
    handleStartBot,
    handleStopBot,
    handleDeleteAccount,
  } = useMt5BotActions({
    loadState,
    mt5FullAccess,
    onNotice: pushNotice,
    onClearNotice: clearNotice,
    setBotTokenEntitlements,
  });
  const {
    brokerKey,
    filteredAccounts,
    selectedAccount,
    latestDeployment,
    selectedDeployment,
    statusDeployment,
    activeStopDeploymentId,
    selectedAccountHasActiveBot,
    telegramUserHasOtherActiveBot,
    selectedBot,
    controlsLocked,
    backgroundPollingPaused,
    selectedBotDisplayName,
    selectedBotProfile,
    activeBotEntitlement,
    botAccessReady,
    actionHint,
  } = useMt5BotDerivedState({
    selectedBroker,
    selectedAccountId,
    selectedBotName,
    accounts,
    deployments,
    bots,
    botTokenEntitlements,
    mt5FullAccess,
    loadingState,
    refreshingState,
    startingBot,
    stoppingBot,
    deletingAccount,
    unlockingBotToken,
  });
  const deleteConfirmationActive = Boolean(selectedAccount && deleteConfirmAccountId === selectedAccount.id);
  const lotControlDisabled = controlsLocked || selectedAccountHasActiveBot;

  const handleRefreshState = useCallback(async () => {
    setNotice(null);
    await refreshState();
  }, [refreshState]);
  const onUnlockBotToken = useCallback(async () => {
    await handleUnlockBotToken({
      selectedAccount,
      selectedBot,
      botTokenInput,
      onResetBotTokenInput: () => setBotTokenInput(""),
      onRequireTerms,
    });
  }, [botTokenInput, handleUnlockBotToken, onRequireTerms, selectedAccount, selectedBot]);

  const onStartBot = useCallback(async () => {
    await handleStartBot({
      selectedAccount,
      selectedBot,
      selectedAccountHasActiveBot,
      telegramUserHasOtherActiveBot,
      botAccessReady,
      lotSizeInput,
      latestDeployment,
      activeBotEntitlementId: activeBotEntitlement?.entitlement_id
        ? String(activeBotEntitlement.entitlement_id)
        : undefined,
      mt5FullAccess,
      onRequireTerms,
    });
  }, [
    activeBotEntitlement?.entitlement_id,
    botAccessReady,
    handleStartBot,
    latestDeployment,
    lotSizeInput,
    mt5FullAccess,
    onRequireTerms,
    selectedAccount,
    selectedAccountHasActiveBot,
    selectedBot,
    telegramUserHasOtherActiveBot,
  ]);

  const onStopBot = useCallback(async () => {
    await handleStopBot({
      selectedAccount,
      activeStopDeploymentId,
    });
  }, [activeStopDeploymentId, handleStopBot, selectedAccount]);

  const onDeleteAccount = useCallback(async () => {
    await handleDeleteAccount({
      selectedAccount,
      selectedAccountHasActiveBot,
      brokerKey,
      onSetSelectedAccountId: setSelectedAccountId,
      onResetBotTokenInput: () => setBotTokenInput(""),
    });
  }, [brokerKey, handleDeleteAccount, selectedAccount, selectedAccountHasActiveBot]);

  useEffect(() => {
    setSelectedAccountId(null);
    setSelectedBotName("");
    setLotSizeInput(LOT_SIZE_DEFAULT);
    setLotEditorOpen(false);
    setNotice(null);
    void loadState({ silentErrors: true, includeBots: true });
  }, [loadState, selectedBroker]);

  useEffect(() => {
    if (!filteredAccounts.length) {
      setSelectedAccountId(null);
      return;
    }

    if (!filteredAccounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(filteredAccounts[0]?.id ?? null);
    }
  }, [filteredAccounts, selectedAccountId]);

  useEffect(() => {
    if (!bots.length) {
      setSelectedBotName("");
      return;
    }

    if (selectedDeployment?.bot_name && bots.some((bot) => bot.bot_name === selectedDeployment.bot_name)) {
      setSelectedBotName(selectedDeployment.bot_name);
      return;
    }

    if (preferredBotName && bots.some((bot) => bot.bot_name === preferredBotName)) {
      setSelectedBotName(preferredBotName);
      return;
    }

    if (!bots.some((bot) => bot.bot_name === selectedBotName)) {
      setSelectedBotName(bots[0]?.bot_name ?? "");
    }
  }, [bots, preferredBotName, selectedBotName, selectedDeployment?.bot_name]);

  useEffect(() => {
    void loadBotTokenEntitlements(selectedAccount?.id ?? null, { silentErrors: true });
  }, [loadBotTokenEntitlements, selectedAccount?.id]);

  useEffect(() => {
    setBotTokenInput("");
    setDeleteConfirmAccountId(null);
    setLotEditorOpen(false);
  }, [selectedAccount?.id, selectedBot?.bot_name, setDeleteConfirmAccountId]);

  useEffect(() => {
    if (lotEditorOpen) {
      return;
    }
    setLotSizeInput(getDeploymentLotSize(latestDeployment?.config_json) ?? LOT_SIZE_DEFAULT);
  }, [latestDeployment?.config_json, latestDeployment?.id, lotEditorOpen, selectedAccount?.id, selectedBot?.bot_name]);

  useEffect(() => {
    if (backgroundPollingPaused) {
      return;
    }
    const inTransition =
      startingBot ||
      stoppingBot ||
      isTransitionalDeploymentStatus(latestDeployment?.status) ||
      String(latestDeployment?.status || "").trim().toLowerCase() === "queued";
    const refreshMs = inTransition
      ? CONTROL_PLANE_TRANSITION_REFRESH_MS
      : CONTROL_PLANE_REFRESH_MS;
    const intervalId = window.setInterval(() => {
      void loadState({ silentErrors: true, spinner: false, includeBots: false });
    }, refreshMs);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [
    backgroundPollingPaused,
    latestDeployment?.status,
    loadState,
    selectedBroker,
    startingBot,
    stoppingBot,
  ]);

  return (
    <section className="rounded-3xl border border-cyan-300/15 bg-transparent p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-100">
            Điều khiển bot
          </p>
          <h4 className="mt-2 text-lg font-semibold text-white">
            Bật/tắt bot
          </h4>
        </div>
        <button
          type="button"
          onClick={handleRefreshState}
          disabled={controlsLocked}
          className="rounded-2xl border border-white/10 bg-transparent p-3 text-white transition hover:border-white/20 hover:bg-black/[0.06] disabled:cursor-not-allowed disabled:opacity-60"
          aria-label="Làm mới trạng thái bot"
        >
          {refreshingState ? (
            <Loader2 className="h-5 w-5 animate-spin" strokeWidth={1.9} />
          ) : (
            <RefreshCcw className="h-5 w-5" strokeWidth={1.9} />
          )}
        </button>
      </div>

      <div className="mt-4 space-y-4">
        {loadingState ? (
          <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/5 px-4 py-4 text-sm text-cyan-100">
            <div className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
              Đang nạp trạng thái bot...
            </div>
          </div>
        ) : filteredAccounts.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-5">
            <p className="text-sm font-semibold text-white">
              Chưa có account nào
            </p>
            <p className="mt-2 text-sm leading-6 text-cyber-muted">
              Hãy kết nối account ở form bên trên trước, rồi panel bật/tắt bot sẽ tự sẵn sàng.
            </p>
          </div>
        ) : (
          <>
            <div className="grid gap-3">
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                    Chọn account
                  </span>
                  <button
                    type="button"
                    onClick={onDeleteAccount}
                    disabled={controlsLocked || !selectedAccount}
                    className={`inline-flex min-h-[34px] shrink-0 items-center justify-center gap-1.5 rounded-2xl border px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
                      deleteConfirmationActive
                        ? "border-rose-300/35 bg-rose-300/15 text-rose-50 hover:bg-rose-300/20"
                        : "border-white/10 bg-transparent text-cyber-muted hover:border-rose-300/30 hover:bg-rose-300/10 hover:text-rose-100"
                    }`}
                  >
                    {deletingAccount ? (
                      <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                    ) : (
                      <Trash2 className="h-4 w-4" strokeWidth={1.9} />
                    )}
                    <span>{deleteConfirmationActive ? "Xác nhận xóa" : "Xóa account"}</span>
                  </button>
                </div>
                <select
                  className={selectClassName}
                  value={selectedAccount?.id ?? ""}
                  disabled={controlsLocked}
                  onChange={(event) => {
                    const nextValue = Number(event.target.value || "0");
                    setSelectedAccountId(Number.isFinite(nextValue) && nextValue > 0 ? nextValue : null);
                  }}
                >
                  {filteredAccounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.login} · {humanizeAccountStatus(account)}
                    </option>
                  ))}
                </select>
              </div>

              <label className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                  Chọn bot
                </span>
                <button
                  type="button"
                  className={`${selectClassName} flex min-h-[50px] items-center gap-2 text-left font-semibold disabled:cursor-not-allowed disabled:opacity-60`}
                  disabled={controlsLocked}
                  onClick={() => {
                    if (!selectedBot) return;
                    setSelectedBotName(selectedBot.bot_name);
                    onSelectedBotChange?.(selectedBot.bot_name);
                  }}
                >
                  <span>{selectedBot?.display_name || "Gs Algo"}</span>
                </button>
              </label>
              {botCatalogError ? (
                <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-100">
                  {bots.length > 0
                    ? "Chưa tải được bản mới của danh sách bot. Panel đang giữ danh sách đã tải để không làm mất trạng thái token."
                    : "Chưa tải được danh sách bot. Token và nút bật bot vẫn được khóa cho tới khi chọn được bot."}
                </div>
              ) : null}

              <div className="rounded-2xl border border-cyan-300/15 bg-transparent px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                      Token mở quyền bot
                    </p>
                    <p className="mt-1 text-xs leading-5 text-cyber-muted">
                      {mt5FullAccess
                        ? "User này đã được mở quyền Mini App, không cần token."
                        : "Chọn bot xong nhập token để mở quyền cho account này."}
                    </p>
                  </div>
                  <span
                    className={`${statusPillClassName} ${
                      botAccessReady
                        ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                        : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                    }`}
                  >
                    {botAccessReady ? "Đã mở" : "Cần token"}
                  </span>
                </div>

                {mt5FullAccess ? (
                  <p className="mt-3 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-100">
                    Tài khoản Telegram này có quyền dùng bot không cần token.
                  </p>
                ) : activeBotEntitlement ? (
                  <p className="mt-3 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-100">
                    Bot này đã được mở quyền đến {formatTokenExpiry(activeBotEntitlement.expires_at)}.
                  </p>
                ) : (
                  <div className="mt-3 grid gap-2">
                    <input
                      className={selectClassName}
                      value={botTokenInput}
                      onChange={(event) => setBotTokenInput(event.target.value)}
                      placeholder="Dán token bạn nhận được"
                      autoComplete="off"
                      disabled={controlsLocked || !selectedAccount || !isMt5AccountReady(selectedAccount) || !selectedBot}
                    />
                    <button
                      type="button"
                      onClick={onUnlockBotToken}
                      disabled={
                        controlsLocked ||
                        !selectedAccount ||
                        !isMt5AccountReady(selectedAccount) ||
                        !selectedBot ||
                        !botTokenInput.trim()
                      }
                      className="flex min-h-[46px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-300/10 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/40 hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {unlockingBotToken ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                          Đang kiểm tra token
                        </>
                      ) : (
                        "Mở quyền bot"
                      )}
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-white/10 bg-transparent px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-cyan-100">
                      <ServerCog className="h-4 w-4 shrink-0" strokeWidth={1.9} />
                      <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">Account</p>
                    </div>
                    <p className="mt-2 truncate text-sm font-semibold text-white">
                      {selectedAccount ? selectedAccount.login : "Chưa chọn account"}
                    </p>
                    <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">
                      {selectedAccount?.server || selectedBroker}
                    </p>
                  </div>
                  <span className={`${statusPillClassName} ${getAccountStatusPillClassName(selectedAccount)}`}>
                    {humanizeAccountStatus(selectedAccount)}
                  </span>
                </div>
              </div>

              <div className="rounded-2xl border border-white/10 bg-transparent px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-cyan-100">
                      <Bot className="h-4 w-4 shrink-0" strokeWidth={1.9} />
                      <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">Bot</p>
                    </div>
                    <p className="mt-2 truncate text-sm font-semibold text-white">{selectedBotDisplayName}</p>
                    <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">{selectedBotProfile}</p>
                  </div>
                  <span
                    className={`${statusPillClassName} ${getDeploymentStatusPillClassName(
                      statusDeployment,
                      selectedAccount
                    )}`}
                  >
                    {humanizeDeploymentStatus(statusDeployment, selectedAccount)}
                  </span>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-cyan-300/15 bg-transparent px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                    Lot
                  </p>
                  <p className="mt-1 truncate text-sm font-semibold text-white">
                    {lotSizeInput}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={lotEditorOpen ? "Đóng chỉnh lot" : "Chỉnh lot"}
                  title={lotEditorOpen ? "Đóng chỉnh lot" : "Chỉnh lot"}
                  onClick={() => setLotEditorOpen((open) => !open)}
                  disabled={lotControlDisabled}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/10 text-cyan-50 transition hover:border-cyan-300/40 hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Pencil className="h-4 w-4" strokeWidth={1.9} />
                </button>
              </div>

              {lotEditorOpen && (
                <label className="mt-3 block space-y-2">
                  <span className="text-xs font-semibold uppercase tracking-[0.14em] text-cyber-muted">
                    Số lot
                  </span>
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    inputMode="decimal"
                    value={lotSizeInput}
                    disabled={lotControlDisabled}
                    onChange={(event) => setLotSizeInput(event.target.value)}
                    className="w-full rounded-2xl border border-white/10 bg-transparent px-4 py-3 text-sm font-semibold text-white outline-none transition placeholder:text-cyber-muted/70 focus:border-cyan-300/40 focus:bg-black/[0.06] disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </label>
              )}
            </div>

            {termsEnabled && (
              <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-50">
                <div className="flex gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.9} />
                  <p>{MINIAPP_RISK_WARNING_SHORT}</p>
                </div>
              </div>
            )}

            <div className="grid gap-2 sm:grid-cols-2">
              <button
                type="button"
                onClick={onStartBot}
                disabled={
                  controlsLocked ||
                  !selectedAccount ||
                  !isMt5AccountReady(selectedAccount) ||
                  !selectedBot ||
                  !botAccessReady ||
                  selectedAccountHasActiveBot ||
                  telegramUserHasOtherActiveBot
                }
                className="flex min-h-[50px] items-center justify-center gap-2 rounded-2xl border border-emerald-300/25 bg-emerald-300/[0.06] px-4 py-3 text-sm font-semibold text-emerald-50 transition hover:border-emerald-300/40 hover:bg-emerald-300/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {startingBot ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                    Đang bật bot...
                  </>
                ) : (
                  <>
                    <PlayCircle className="h-4 w-4" strokeWidth={1.9} />
                    Bật bot
                  </>
                )}
              </button>

              <button
                type="button"
                onClick={onStopBot}
                disabled={
                  controlsLocked ||
                  !activeStopDeploymentId
                }
                className="flex min-h-[50px] items-center justify-center gap-2 rounded-2xl border border-rose-300/25 bg-rose-300/[0.06] px-4 py-3 text-sm font-semibold text-rose-50 transition hover:border-rose-300/40 hover:bg-rose-300/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {stoppingBot ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                    Đang tắt
                  </>
                ) : (
                  <>
                    <PauseCircle className="h-4 w-4" strokeWidth={1.9} />
                    Tắt bot
                  </>
                )}
              </button>
            </div>

            {(() => {
              // Show the real Windows-runner sub-state during transitions so user
              // sees genuine progress (executor_preparing, executor_ready, etc.)
              // instead of a silent spinner. Falls back to actionHint when idle.
              const progressText = (startingBot || stoppingBot || isTransitionalDeploymentStatus(latestDeployment?.status))
                ? humanizeDeploymentProgress(latestDeployment ?? selectedDeployment) ??
                  (startingBot
                    ? "Đang bật bot. vui lòng đợi 15-25 giây..."
                    : stoppingBot
                      ? "Đang tắt bot, vui lòng đợi vài giây..."
                      : null)
                : null;
              if (progressText) {
                return (
                  <div className="flex items-center gap-2 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-4 py-3 text-sm leading-6 text-cyan-100">
                    <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                    <span>{progressText}</span>
                  </div>
                );
              }
              if (actionHint) {
                return (
                  <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100">
                    {actionHint}
                  </div>
                );
              }
              return null;
            })()}
          </>
        )}

        {notice && (
          <div
            role="status"
            aria-live="polite"
            className={`rounded-2xl border px-4 py-3 text-sm leading-6 ${toneStyles[notice.tone]}`}
          >
            {notice.message}
          </div>
        )}
      </div>
    </section>
  );
}
