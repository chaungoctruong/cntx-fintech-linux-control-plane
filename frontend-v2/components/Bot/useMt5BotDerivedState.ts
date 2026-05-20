"use client";

import {
  entitlementMatchesBot,
  botSupportsBroker,
  formatBotProfileClass,
  formatBotDisplayName,
  getLatestDeploymentForAccount,
  isActiveDeploymentStatus,
} from "@/components/Bot/mt5ControlUtils";
import { getActionHint } from "@/components/Bot/mt5ControlMessages";
import type { MT5AccountItem, MT5BotCatalogItem, MT5BotTokenEntitlement, MT5DeploymentItem } from "@/lib/api";

type UseMt5BotDerivedStateArgs = {
  selectedBroker: string;
  selectedAccountId: number | null;
  selectedBotName: string;
  accounts: MT5AccountItem[];
  deployments: MT5DeploymentItem[];
  bots: MT5BotCatalogItem[];
  botTokenEntitlements: MT5BotTokenEntitlement[];
  mt5FullAccess: boolean;
  loadingState: boolean;
  refreshingState: boolean;
  startingBot: boolean;
  stoppingBot: boolean;
  deletingAccount: boolean;
  unlockingBotToken: boolean;
};

export function useMt5BotDerivedState({
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
}: UseMt5BotDerivedStateArgs) {
  const brokerKey = selectedBroker.trim().toLowerCase();
  const filteredAccounts = accounts.filter((account) => account.broker.trim().toLowerCase() === brokerKey);
  const brokerBots = bots.filter((bot) => botSupportsBroker(bot, selectedBroker));
  const selectedAccount =
    filteredAccounts.find((account) => account.id === selectedAccountId) ?? filteredAccounts[0] ?? null;
  const activeDeployment =
    deployments.find((deployment) => deployment.id === selectedAccount?.active_deployment_id) ?? null;
  const latestDeployment = getLatestDeploymentForAccount(deployments, selectedAccount?.id ?? null);
  const latestDeploymentIsCurrent =
    !activeDeployment && isActiveDeploymentStatus(latestDeployment?.status) ? latestDeployment : null;
  const selectedDeployment = activeDeployment ?? latestDeploymentIsCurrent;
  const statusDeployment = selectedDeployment ?? latestDeployment;
  const activeStopDeploymentId =
    selectedAccount?.active_deployment_id ??
    (isActiveDeploymentStatus(selectedDeployment?.status) ? (selectedDeployment?.id ?? null) : null);
  const selectedAccountHasActiveBot =
    Boolean(activeStopDeploymentId) || isActiveDeploymentStatus(selectedDeployment?.status);
  const telegramUserHasActiveBot =
    accounts.some(
      (account) => Boolean(account.active_deployment_id) || isActiveDeploymentStatus(account.active_deployment_status)
    ) || deployments.some((deployment) => isActiveDeploymentStatus(deployment.status));
  const telegramUserHasOtherActiveBot = !mt5FullAccess && telegramUserHasActiveBot && !selectedAccountHasActiveBot;
  const selectedBot =
    brokerBots.find((bot) => bot.bot_name === selectedBotName) ??
    bots.find((bot) => bot.bot_name === selectedDeployment?.bot_name) ??
    brokerBots[0] ??
    null;
  const controlsLocked =
    loadingState || refreshingState || startingBot || stoppingBot || deletingAccount || unlockingBotToken;
  const backgroundPollingPaused =
    refreshingState || startingBot || stoppingBot || deletingAccount || unlockingBotToken;
  const selectedDeploymentBotName =
    bots.find((bot) => bot.bot_name === selectedDeployment?.bot_name)?.display_name ||
    formatBotDisplayName(selectedDeployment?.bot_name || selectedDeployment?.bot_code) ||
    "Chưa chạy";
  const selectedBotDisplayName =
    selectedDeployment?.bot_name || selectedDeployment?.bot_code
      ? selectedDeploymentBotName
      : formatBotDisplayName(selectedBot?.display_name || selectedBot?.bot_name) || "Chưa chọn bot";
  const selectedBotProfile = formatBotProfileClass(selectedDeployment?.profile_class || selectedBot?.profile_class);
  const activeBotEntitlement =
    selectedAccount == null
      ? null
      : botTokenEntitlements.find(
          (entitlement) =>
            Number(entitlement.account_id || 0) === selectedAccount.id &&
            String(entitlement.status || "").toLowerCase() === "active" &&
            entitlementMatchesBot(entitlement, selectedBot)
        ) ?? null;
  const botAccessReady = mt5FullAccess || Boolean(activeBotEntitlement);
  const actionHint = telegramUserHasOtherActiveBot
    ? "Tài khoản Telegram này đang có bot hoạt động. Tắt bot hiện tại trước khi bật bot khác."
    : getActionHint({
        selectedAccount,
        selectedBot,
        selectedDeployment: statusDeployment,
        controlsLocked,
        refreshingState,
        startingBot,
        stoppingBot,
      });

  return {
    brokerKey,
    brokerBots,
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
  };
}
