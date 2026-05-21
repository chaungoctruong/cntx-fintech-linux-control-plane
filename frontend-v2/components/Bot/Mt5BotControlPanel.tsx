"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  Loader2,
  PauseCircle,
  Pencil,
  PlayCircle,
  RefreshCcw,
  Search,
  ServerCog,
  Trash2,
  X,
} from "lucide-react";

import {
  type MT5AccountItem,
} from "@/lib/api";
import { MINIAPP_RISK_WARNING_SHORT } from "@/components/Bot/MiniappTermsModal";
import {
  LOT_SIZE_DEFAULT,
  entitlementMatchesBot,
  formatBotProfileClass,
  formatTokenExpiry,
  getDeploymentLotSize,
  getAccountStatusPillClassName,
  getDeploymentStatusPillClassName,
  getMt5AccountLoginIssueMessage,
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

type SelectorSheet = "account" | "bot" | null;

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

function normalizeSelectorQuery(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

type Mt5BotControlPanelProps = {
  selectedBroker: string;
  preferredBotName?: string;
  onSelectedBotChange?: (botName: string) => void;
  onReconnectAccount?: (account: MT5AccountItem) => void;
  mt5FullAccess?: boolean;
  onRequireTerms?: (afterAccept?: () => void) => boolean;
  termsEnabled?: boolean;
};

export default function Mt5BotControlPanel({
  selectedBroker,
  preferredBotName,
  onSelectedBotChange,
  onReconnectAccount,
  mt5FullAccess = false,
  onRequireTerms,
  termsEnabled = false,
}: Mt5BotControlPanelProps) {
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [selectedBotName, setSelectedBotName] = useState("");
  const [botTokenInput, setBotTokenInput] = useState("");
  const [lotSizeInput, setLotSizeInput] = useState(LOT_SIZE_DEFAULT);
  const [lotEditorOpen, setLotEditorOpen] = useState(false);
  const [selectorSheet, setSelectorSheet] = useState<SelectorSheet>(null);
  const [selectorQuery, setSelectorQuery] = useState("");
  const [selectorPortal, setSelectorPortal] = useState<HTMLElement | null>(null);
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
    brokerBots,
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
  const selectedAccountLoginIssue = getMt5AccountLoginIssueMessage(selectedAccount);
  const showSelectedAccountLoginIssue =
    selectedAccountLoginIssue &&
    String(selectedAccount?.status || "").trim().toLowerCase() === "login_failed";
  const selectedAccountReady = isMt5AccountReady(selectedAccount);
  const selectedAccountStatus = humanizeAccountStatus(selectedAccount);
  const selectedAccountServer = selectedAccount?.server || selectedBroker || "Server chưa rõ";
  const selectedAccountTitle = selectedAccount?.login || "Chọn tài khoản MT5";
  const selectedAccountSubtitle = selectedAccount
    ? `${selectedAccount.broker || selectedBroker} · ${selectedAccountServer}`
    : "Chưa có tài khoản được chọn";
  const selectedBotTitle = selectedBotDisplayName || "Chọn bot";
  const selectedBotSubtitle = selectedBot
    ? `${formatBotProfileClass(selectedBot.profile_class)} · ${selectedBroker}`
    : "Chọn bot phù hợp với broker này";
  const selectorQueryNormalized = normalizeSelectorQuery(selectorQuery);
  const visibleSelectorAccounts = selectorQueryNormalized
    ? filteredAccounts.filter((account) =>
        normalizeSelectorQuery(
          [account.login, account.broker, account.server, humanizeAccountStatus(account)].join(" ")
        ).includes(selectorQueryNormalized)
      )
    : filteredAccounts;
  const visibleSelectorBots = selectorQueryNormalized
    ? brokerBots.filter((bot) =>
        normalizeSelectorQuery(
          [bot.display_name, bot.bot_name, bot.bot_id, formatBotProfileClass(bot.profile_class)].join(" ")
        ).includes(selectorQueryNormalized)
      )
    : brokerBots;

  const selectorOverlay = (
    <AnimatePresence>
      {selectorSheet && (
        <motion.div
          className="fixed inset-0 z-[80] flex min-h-[100dvh] items-center justify-center overscroll-contain bg-black/55 px-3 py-6 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setSelectorSheet(null)}
        >
          <motion.div
            className="flex max-h-[calc(100dvh-72px)] w-full max-w-md flex-col overflow-hidden rounded-[28px] border border-cyan-300/15 bg-[#05090f] shadow-[0_24px_70px_rgba(0,0,0,0.62)]"
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-4">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                  {selectorSheet === "account" ? selectedBroker : "Bot MT5"}
                </p>
                <h4 className="mt-1 text-base font-semibold text-white">
                  {selectorSheet === "account" ? "Chọn tài khoản MT5" : "Chọn bot"}
                </h4>
              </div>
              <button
                type="button"
                onClick={() => setSelectorSheet(null)}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-black/[0.12] text-cyber-muted transition hover:border-white/20 hover:text-white"
                aria-label="Đóng bảng chọn"
              >
                <X className="h-5 w-5" strokeWidth={1.9} />
              </button>
            </div>

            <div className="shrink-0 border-b border-white/10 px-4 py-3">
              <label className="relative block">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-cyber-muted"
                  strokeWidth={1.9}
                />
                <input
                  type="search"
                  value={selectorQuery}
                  onChange={(event) => setSelectorQuery(event.target.value)}
                  placeholder={selectorSheet === "account" ? "Tìm login, server..." : "Tìm tên bot..."}
                  className="h-11 w-full rounded-2xl border border-white/10 bg-black/[0.12] pl-10 pr-3 text-sm font-medium text-white outline-none transition placeholder:text-cyber-muted focus:border-cyan-300/35"
                />
              </label>
            </div>

            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain px-4 py-4 [-webkit-overflow-scrolling:touch]">
              {selectorSheet === "account" ? (
                visibleSelectorAccounts.length > 0 ? (
                  visibleSelectorAccounts.map((account) => {
                    const accountSelected = account.id === selectedAccount?.id;
                    const accountReady = isMt5AccountReady(account);
                    const accountLoginFailed = String(account.status || "").trim().toLowerCase() === "login_failed";
                    const accountStatusClass = getAccountStatusPillClassName(account);
                    return (
                      <div
                        key={account.id}
                        className={`rounded-3xl border p-3 transition ${
                          accountSelected
                            ? "border-cyan-300/35 bg-cyan-300/10"
                            : accountLoginFailed
                              ? "border-rose-300/25 bg-rose-300/[0.07]"
                              : "border-white/10 bg-black/[0.08]"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedAccountId(account.id);
                            setSelectorSheet(null);
                          }}
                          className="flex w-full items-start gap-3 text-left"
                        >
                          <div
                            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border ${
                              accountReady
                                ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                : accountLoginFailed
                                  ? "border-rose-300/25 bg-rose-300/10 text-rose-100"
                                  : "border-cyan-300/20 bg-cyan-300/10 text-cyan-100"
                            }`}
                          >
                            {accountSelected ? (
                              <Check className="h-5 w-5" strokeWidth={1.9} />
                            ) : (
                              <ServerCog className="h-5 w-5" strokeWidth={1.9} />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-white">{account.login}</p>
                            <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">
                              {account.broker || selectedBroker} · {account.server || "Server chưa rõ"}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              <span className={`${statusPillClassName} ${accountStatusClass}`}>
                                {humanizeAccountStatus(account)}
                              </span>
                              {accountSelected ? (
                                <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-100">
                                  Đang chọn
                                </span>
                              ) : null}
                            </div>
                          </div>
                        </button>

                        {accountLoginFailed && onReconnectAccount ? (
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedAccountId(account.id);
                              setSelectorSheet(null);
                              onReconnectAccount(account);
                            }}
                            className="mt-3 flex min-h-[40px] w-full items-center justify-center rounded-2xl border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs font-semibold text-rose-50 transition hover:border-rose-300/40 hover:bg-rose-300/15"
                          >
                            Kết nối lại
                          </button>
                        ) : null}
                      </div>
                    );
                  })
                ) : (
                  <div className="rounded-3xl border border-dashed border-white/10 bg-black/[0.08] px-4 py-6 text-sm leading-6 text-cyber-muted">
                    Không tìm thấy tài khoản phù hợp.
                  </div>
                )
              ) : visibleSelectorBots.length > 0 ? (
                visibleSelectorBots.map((bot) => {
                  const botSelected = bot.bot_name === selectedBot?.bot_name;
                  const rowAccessReady =
                    mt5FullAccess || botTokenEntitlements.some((entitlement) => entitlementMatchesBot(entitlement, bot));
                  return (
                    <button
                      key={bot.bot_id || bot.bot_name}
                      type="button"
                      onClick={() => {
                        setSelectedBotName(bot.bot_name);
                        onSelectedBotChange?.(bot.bot_name);
                        setSelectorSheet(null);
                      }}
                      className={`w-full rounded-3xl border p-3 text-left transition ${
                        botSelected
                          ? "border-cyan-300/35 bg-cyan-300/10"
                          : "border-white/10 bg-black/[0.08] hover:border-cyan-300/25"
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
                          {botSelected ? (
                            <Check className="h-5 w-5" strokeWidth={1.9} />
                          ) : (
                            <Bot className="h-5 w-5" strokeWidth={1.9} />
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-semibold text-white">{bot.display_name}</p>
                          <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">
                            {formatBotProfileClass(bot.profile_class)}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-100">
                              MT5
                            </span>
                            <span
                              className={`${statusPillClassName} ${
                                rowAccessReady
                                  ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                  : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                              }`}
                            >
                              {rowAccessReady ? "Đã mở" : "Cần mã"}
                            </span>
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })
              ) : (
                <div className="rounded-3xl border border-dashed border-white/10 bg-black/[0.08] px-4 py-6 text-sm leading-6 text-cyber-muted">
                  Không tìm thấy bot phù hợp.
                </div>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

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
    setSelectorSheet(null);
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
    if (!brokerBots.length) {
      setSelectedBotName("");
      return;
    }

    if (selectedDeployment?.bot_name && brokerBots.some((bot) => bot.bot_name === selectedDeployment.bot_name)) {
      setSelectedBotName(selectedDeployment.bot_name);
      return;
    }

    if (preferredBotName && brokerBots.some((bot) => bot.bot_name === preferredBotName)) {
      setSelectedBotName(preferredBotName);
      return;
    }

    if (!brokerBots.some((bot) => bot.bot_name === selectedBotName)) {
      setSelectedBotName(brokerBots[0]?.bot_name ?? "");
    }
  }, [brokerBots, preferredBotName, selectedBotName, selectedDeployment?.bot_name]);

  useEffect(() => {
    void loadBotTokenEntitlements(selectedAccount?.id ?? null, { silentErrors: true });
  }, [loadBotTokenEntitlements, selectedAccount?.id]);

  useEffect(() => {
    setSelectorPortal(document.body);
  }, []);

  useEffect(() => {
    setBotTokenInput("");
    setDeleteConfirmAccountId(null);
    setLotEditorOpen(false);
  }, [selectedAccount?.id, selectedBot?.bot_name, setDeleteConfirmAccountId]);

  useEffect(() => {
    if (!selectorSheet) {
      setSelectorQuery("");
    }
  }, [selectorSheet]);

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
    <>
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
              Chưa có tài khoản MT5
            </p>
            <p className="mt-2 text-sm leading-6 text-cyber-muted">
              Kết nối tài khoản ở form bên trên, bảng điều khiển bot sẽ tự sẵn sàng.
            </p>
          </div>
        ) : (
          <>
            <div className="grid gap-3">
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                    Chọn tài khoản
                  </span>
                  <span className="rounded-full border border-white/10 bg-black/[0.08] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyber-muted">
                    {filteredAccounts.length} tài khoản
                  </span>
                </div>

                <button
                  type="button"
                  onClick={() => setSelectorSheet("account")}
                  disabled={controlsLocked}
                  className={`w-full rounded-3xl border px-4 py-4 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${
                    selectedAccountReady
                      ? "border-emerald-300/25 bg-emerald-300/[0.06] hover:border-emerald-300/40"
                      : showSelectedAccountLoginIssue
                        ? "border-rose-300/25 bg-rose-300/[0.07] hover:border-rose-300/40"
                        : "border-white/10 bg-black/[0.08] hover:border-cyan-300/25"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
                      <ServerCog className="h-5 w-5" strokeWidth={1.9} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-base font-semibold text-white">{selectedAccountTitle}</p>
                      <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">{selectedAccountSubtitle}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <span className={`${statusPillClassName} ${getAccountStatusPillClassName(selectedAccount)}`}>
                          {selectedAccountStatus}
                        </span>
                      </div>
                    </div>
                    <ChevronDown className="mt-1 h-5 w-5 shrink-0 text-cyber-muted" strokeWidth={1.9} />
                  </div>
                </button>

                {selectedAccount ? (
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={onDeleteAccount}
                      disabled={controlsLocked || !selectedAccount}
                      className={`inline-flex min-h-[36px] shrink-0 items-center justify-center gap-1.5 rounded-2xl border px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
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
                      <span>{deleteConfirmationActive ? "Xác nhận xóa" : "Xóa tài khoản"}</span>
                    </button>
                  </div>
                ) : null}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-cyber-muted">
                    Chọn bot
                  </span>
                  <span
                    className={`${statusPillClassName} ${
                      botAccessReady
                        ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                        : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                    }`}
                  >
                    {botAccessReady ? "Đã mở" : "Cần mã"}
                  </span>
                </div>

                <button
                  type="button"
                  onClick={() => setSelectorSheet("bot")}
                  disabled={controlsLocked || brokerBots.length === 0}
                  className="w-full rounded-3xl border border-white/10 bg-black/[0.08] px-4 py-4 text-left transition hover:border-cyan-300/25 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
                      <Bot className="h-5 w-5" strokeWidth={1.9} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-base font-semibold text-white">{selectedBotTitle}</p>
                      <p className="mt-1 truncate text-xs leading-5 text-cyber-muted">{selectedBotSubtitle}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-100">
                          MT5
                        </span>
                        <span className="rounded-full border border-white/10 bg-black/[0.12] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyber-muted">
                          {selectedBroker}
                        </span>
                      </div>
                    </div>
                    <ChevronDown className="mt-1 h-5 w-5 shrink-0 text-cyber-muted" strokeWidth={1.9} />
                  </div>
                </button>
              </div>
              {brokerBots.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/10 bg-transparent px-4 py-3 text-xs leading-5 text-cyber-muted">
                  Chưa có bot nào khả dụng cho sàn {selectedBroker}.
                </div>
              ) : null}
              {botCatalogError ? (
                <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-100">
                  {bots.length > 0
                    ? "Chưa tải được danh sách bot mới. Bảng điều khiển vẫn giữ dữ liệu gần nhất để không gián đoạn thao tác."
                    : "Chưa tải được danh sách bot. Nhập mã và bật bot sẽ mở lại khi danh sách sẵn sàng."}
                </div>
              ) : null}

              <div className="rounded-2xl border border-cyan-300/15 bg-transparent px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100">
                      Mã kích hoạt bot
                    </p>
                    <p className="mt-1 text-xs leading-5 text-cyber-muted">
                      {mt5FullAccess
                        ? "Tài khoản Telegram này đã được mở quyền, không cần nhập mã."
                        : "Nhập mã kích hoạt để mở quyền bot cho tài khoản này."}
                    </p>
                  </div>
                  <span
                    className={`${statusPillClassName} ${
                      botAccessReady
                        ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                        : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                    }`}
                  >
                    {botAccessReady ? "Đã mở" : "Cần mã"}
                  </span>
                </div>

                {mt5FullAccess ? (
                  <p className="mt-3 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-100">
                    Tài khoản Telegram này có quyền dùng bot không cần mã kích hoạt.
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
                      placeholder="Dán mã kích hoạt bạn nhận được"
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
                          Đang kiểm tra mã
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
                      <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">Tài khoản</p>
                    </div>
                    <p className="mt-2 truncate text-sm font-semibold text-white">
                      {selectedAccount ? selectedAccount.login : "Chưa chọn tài khoản"}
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

            {showSelectedAccountLoginIssue ? (
              <div className="rounded-2xl border border-rose-400/25 bg-rose-400/10 px-4 py-3 text-sm leading-6 text-rose-100">
                {selectedAccountLoginIssue}
              </div>
            ) : null}

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
                    ? "Đang bật bot. Đợi 15-25 giây..."
                    : stoppingBot
                      ? "Đang tắt bot, đợi vài giây..."
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
    {selectorPortal ? createPortal(selectorOverlay, selectorPortal) : null}
    </>
  );
}
