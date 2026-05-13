"use client";

import { useCallback, useState, type Dispatch, type SetStateAction } from "react";

import {
  claimMt5BotToken,
  deleteMt5Account,
  startMt5Deployment,
  stopMt5Deployment,
  type MT5AccountItem,
  type MT5BotCatalogItem,
  type MT5BotTokenEntitlement,
  type MT5DeploymentItem,
} from "@/lib/api";
import {
  getDeploymentByIdForAccount,
  getLatestDeploymentForAccount,
  getStartResponseDeploymentId,
  isMt5AccountReady,
  normalizeDeploymentId,
  parsePositiveDecimalInput,
} from "@/components/Bot/mt5ControlUtils";
import { getDeploymentFailureMessage, getFriendlyMt5ActionError } from "@/components/Bot/mt5ControlMessages";

// Poll cadence follows real runner behavior: STOP_BOT usually settles in a few
// seconds, START_BOT commonly takes ~15-25s while Windows opens/attaches MT5.
// Keep a longer tail for cold starts, but poll faster at first for responsive UI.
const STOP_POLL_DELAYS_MS = [
  500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 3500, 4000, 5000,
];
const START_POLL_DELAYS_MS = [
  800, 1000, 1500, 2000, 2500, 3000, 3000, 3500, 4000, 4500,
  5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000,
  5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000,
];

type NoticeTone = "success" | "error" | "info";

type Snapshot = {
  accounts: MT5AccountItem[];
  deployments: MT5DeploymentItem[];
};

type LoadStateFn = (options?: { silentErrors?: boolean; spinner?: boolean; includeBots?: boolean }) => Promise<Snapshot | null>;

type StartPollOptions = {
  deploymentId?: number | null;
  afterDeploymentId?: number | null;
};

type PollResult = {
  settled: boolean;
  success: boolean;
  account: MT5AccountItem | null;
  latestDeployment: MT5DeploymentItem | null;
  outcome: "running" | "stopped" | "failed" | "pending";
};

type UnlockParams = {
  selectedAccount: MT5AccountItem | null;
  selectedBot: MT5BotCatalogItem | null;
  botTokenInput: string;
  onResetBotTokenInput: () => void;
  onRequireTerms?: (afterAccept?: () => void) => boolean;
};

type StartParams = {
  selectedAccount: MT5AccountItem | null;
  selectedBot: MT5BotCatalogItem | null;
  selectedAccountHasActiveBot: boolean;
  telegramUserHasOtherActiveBot: boolean;
  botAccessReady: boolean;
  lotSizeInput: string;
  latestDeployment: MT5DeploymentItem | null;
  activeBotEntitlementId?: string;
  mt5FullAccess: boolean;
  onRequireTerms?: (afterAccept?: () => void) => boolean;
};

type StopParams = {
  selectedAccount: MT5AccountItem | null;
  activeStopDeploymentId: number | null;
};

type DeleteParams = {
  selectedAccount: MT5AccountItem | null;
  selectedAccountHasActiveBot: boolean;
  brokerKey: string;
  onSetSelectedAccountId: (id: number | null) => void;
  onResetBotTokenInput: () => void;
};

type UseMt5BotActionsArgs = {
  loadState: LoadStateFn;
  mt5FullAccess: boolean;
  onNotice: (tone: NoticeTone, message: string) => void;
  onClearNotice: () => void;
  setBotTokenEntitlements: Dispatch<SetStateAction<MT5BotTokenEntitlement[]>>;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isStartFailureStatus(status?: string | null): boolean {
  const normalized = String(status || "").trim().toLowerCase();
  return normalized === "failed" || normalized === "blocked" || normalized === "stopped";
}

export function useMt5BotActions({
  loadState,
  mt5FullAccess,
  onNotice,
  onClearNotice,
  setBotTokenEntitlements,
}: UseMt5BotActionsArgs) {
  const [startingBot, setStartingBot] = useState(false);
  const [stoppingBot, setStoppingBot] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [unlockingBotToken, setUnlockingBotToken] = useState(false);
  const [deleteConfirmAccountId, setDeleteConfirmAccountId] = useState<number | null>(null);

  const pollUntilStable = useCallback(async (
    kind: "start" | "stop",
    accountId: number,
    startOptions?: StartPollOptions
  ): Promise<PollResult> => {
    let latestResult: PollResult = {
      settled: false,
      success: false,
      account: null,
      latestDeployment: null,
      outcome: "pending",
    };

    const schedule = kind === "start" ? START_POLL_DELAYS_MS : STOP_POLL_DELAYS_MS;
    for (const delayMs of schedule) {
      await sleep(delayMs);
      const snapshot = await loadState({ silentErrors: true, spinner: false, includeBots: false });
      if (!snapshot) {
        continue;
      }

      const account = snapshot.accounts.find((item) => item.id === accountId) ?? null;
      const latestDeployment = getLatestDeploymentForAccount(snapshot.deployments, accountId);
      const startDeploymentId = startOptions?.deploymentId ?? null;
      const startAfterDeploymentId = startOptions?.afterDeploymentId ?? null;
      const outcomeDeployment =
        kind === "start"
          ? startDeploymentId
            ? getDeploymentByIdForAccount(snapshot.deployments, accountId, startDeploymentId)
            : startAfterDeploymentId
              ? latestDeployment && latestDeployment.id > startAfterDeploymentId
                ? latestDeployment
                : null
              : latestDeployment
          : latestDeployment;
      latestResult = {
        settled: false,
        success: false,
        account,
        latestDeployment: outcomeDeployment,
        outcome: "pending",
      };

      if (kind === "start") {
        const accountActiveDeploymentId = normalizeDeploymentId(account?.active_deployment_id);
        const accountActiveStatus = String(account?.active_deployment_status || "").trim().toLowerCase();
        const accountMatchesTrackedStart =
          !startDeploymentId || !accountActiveDeploymentId || accountActiveDeploymentId === startDeploymentId;
        const newerFailureDeployment =
          startAfterDeploymentId &&
          latestDeployment &&
          latestDeployment.id > startAfterDeploymentId &&
          isStartFailureStatus(latestDeployment.status)
            ? latestDeployment
            : null;
        const failureDeployment = newerFailureDeployment ?? outcomeDeployment;
        const failureMessage = getDeploymentFailureMessage("start", account, failureDeployment);
        if (failureMessage) {
          return {
            settled: true,
            success: false,
            account,
            latestDeployment: failureDeployment,
            outcome: "failed",
          };
        }
        if (outcomeDeployment?.id && isStartFailureStatus(outcomeDeployment.status)) {
          return {
            settled: true,
            success: false,
            account,
            latestDeployment: outcomeDeployment,
            outcome: "failed",
          };
        }
        if (
          String(outcomeDeployment?.status || "").trim().toLowerCase() === "running" ||
          (accountMatchesTrackedStart && accountActiveStatus === "running")
        ) {
          return {
            settled: true,
            success: true,
            account,
            latestDeployment: outcomeDeployment,
            outcome: "running",
          };
        }
      } else if (!account?.active_deployment_id) {
        const latestStatus = String(latestDeployment?.status || "").trim().toLowerCase();
        if (!latestDeployment || latestStatus === "stopped" || latestStatus === "failed" || latestStatus === "blocked") {
          return {
            settled: true,
            success: true,
            account,
            latestDeployment,
            outcome: "stopped",
          };
        }
      }
    }

    return latestResult;
  }, [loadState]);

  const handleUnlockBotToken = useCallback(async (params: UnlockParams) => {
    onClearNotice();

    if (params.onRequireTerms?.(() => {
      void handleUnlockBotToken(params);
    })) {
      return;
    }

    if (!params.selectedAccount) {
      onNotice("error", "Chọn tài khoản MT5 trước khi nhập mã kích hoạt.");
      return;
    }
    if (!isMt5AccountReady(params.selectedAccount)) {
      onNotice("error", "Tài khoản này chưa sẵn sàng, chưa thể kích hoạt bot.");
      return;
    }
    if (!params.selectedBot) {
      onNotice("error", "Chọn bot trước khi nhập mã kích hoạt.");
      return;
    }

    const token = params.botTokenInput.trim();
    if (!token) {
      onNotice("error", "Vui lòng nhập mã kích hoạt cho bot đã chọn.");
      return;
    }

    setUnlockingBotToken(true);
    try {
      const response = await claimMt5BotToken({
        account_id: params.selectedAccount.id,
        bot_name: params.selectedBot.bot_name,
        token,
      });
      setBotTokenEntitlements((current) => [
        response.entitlement,
        ...current.filter((item) => item.entitlement_id !== response.entitlement.entitlement_id),
      ]);
      params.onResetBotTokenInput();
      onNotice("success", `Đã mở quyền cho bot ${params.selectedBot.display_name}.`);
    } catch (error) {
      onNotice("error", getFriendlyMt5ActionError("start", error));
    } finally {
      setUnlockingBotToken(false);
    }
  }, [onClearNotice, onNotice, setBotTokenEntitlements]);

  const handleStartBot = useCallback(async (params: StartParams) => {
    onClearNotice();

    if (params.onRequireTerms?.(() => {
      void handleStartBot(params);
    })) {
      return;
    }

    if (!params.selectedAccount) {
      onNotice("error", "Chưa có tài khoản MT5 cho sàn này để bật bot.");
      return;
    }
    if (!isMt5AccountReady(params.selectedAccount)) {
      onNotice("error", "Tài khoản này chưa sẵn sàng, chưa thể bật bot.");
      return;
    }
    if (params.selectedAccountHasActiveBot) {
      onNotice("info", "Tài khoản đang có bot chạy. Hãy tắt bot hiện tại trước.");
      return;
    }
    if (params.telegramUserHasOtherActiveBot) {
      onNotice(
        "info",
        "Mỗi tài khoản Telegram chỉ chạy một bot tại một thời điểm. Hãy tắt bot hiện tại trước khi bật bot khác."
      );
      return;
    }
    if (!params.selectedBot) {
      onNotice("error", "Danh sách bot hiện không có lựa chọn khả dụng. Hãy làm mới rồi thử lại.");
      return;
    }
    if (!params.botAccessReady) {
      onNotice("error", "Vui lòng nhập mã kích hoạt cho bot đã chọn trước khi bật bot.");
      return;
    }
    const lotSize = parsePositiveDecimalInput(params.lotSizeInput);
    if (lotSize === null) {
      onNotice("error", "Vui lòng nhập khối lượng (Lot) lớn hơn 0 trước khi bật bot.");
      return;
    }

    setStartingBot(true);
    try {
      onNotice("info", "Đã nhận yêu cầu bật bot. Thường mất khoảng 15-25 giây để mở MT5 và chạy bot.");
      const baselineDeploymentId = params.latestDeployment?.id ?? null;
      const startResponse = await startMt5Deployment({
        account_id: params.selectedAccount.id,
        bot_name: params.selectedBot.bot_name,
        entitlement_id: mt5FullAccess ? undefined : params.activeBotEntitlementId,
        lot_size: lotSize,
      });
      const startedDeploymentId = getStartResponseDeploymentId(startResponse);
      await loadState({ silentErrors: true, spinner: false, includeBots: false });
      const pollResult = await pollUntilStable("start", params.selectedAccount.id, {
        deploymentId: startedDeploymentId,
        afterDeploymentId: baselineDeploymentId,
      });
      if (pollResult.settled && pollResult.success) {
        onNotice("success", "Bot đã bật.");
        return;
      }

      const failureMessage = getDeploymentFailureMessage("start", pollResult.account, pollResult.latestDeployment);
      if (failureMessage) {
        onNotice("error", failureMessage);
        return;
      }
      if (pollResult.settled && !pollResult.success) {
        onNotice("error", "Bot chưa khởi động ổn định. Vui lòng thử lại sau ít phút.");
        return;
      }
      onNotice("info", "Đã nhận yêu cầu bật bot. Mini App sẽ tự cập nhật khi bot chạy.");
    } catch (error) {
      onNotice("error", getFriendlyMt5ActionError("start", error));
    } finally {
      setStartingBot(false);
    }
  }, [loadState, mt5FullAccess, onClearNotice, onNotice, pollUntilStable]);

  const handleStopBot = useCallback(async (params: StopParams) => {
    onClearNotice();

    if (!params.selectedAccount || !params.activeStopDeploymentId) {
      onNotice("error", "Tài khoản chưa có bot đang chạy để tắt.");
      return;
    }

    setStoppingBot(true);
    try {
      await stopMt5Deployment({
        deployment_id: params.activeStopDeploymentId,
        reason: "miniapp_user_stop",
      });
      await loadState({ silentErrors: true, spinner: false, includeBots: false });
      const pollResult = await pollUntilStable("stop", params.selectedAccount.id);
      if (pollResult.settled && pollResult.success) {
        onNotice("success", "Đã tắt");
        return;
      }
      onNotice("info", "Đã nhận yêu cầu tắt, đang cập nhật trạng thái.");
    } catch (error) {
      onNotice("error", getFriendlyMt5ActionError("stop", error));
    } finally {
      setStoppingBot(false);
    }
  }, [loadState, onClearNotice, onNotice, pollUntilStable]);

  const handleDeleteAccount = useCallback(async (params: DeleteParams) => {
    onClearNotice();

    if (!params.selectedAccount) {
      onNotice("error", "Chọn tài khoản MT5 trước khi gỡ.");
      return;
    }
    if (params.selectedAccountHasActiveBot) {
      onNotice("info", "Tài khoản đang có bot chạy. Hãy tắt bot trước khi gỡ tài khoản.");
      return;
    }
    if (deleteConfirmAccountId !== params.selectedAccount.id) {
      setDeleteConfirmAccountId(params.selectedAccount.id);
      onNotice("info", `Bấm "Xác nhận xóa" lần nữa để gỡ tài khoản ${params.selectedAccount.login}.`);
      return;
    }

    setDeletingAccount(true);
    try {
      await deleteMt5Account(params.selectedAccount.id);
      setDeleteConfirmAccountId(null);
      params.onResetBotTokenInput();
      setBotTokenEntitlements([]);
      const snapshot = await loadState({ silentErrors: true, spinner: false, includeBots: false });
      const nextAccount =
        snapshot?.accounts.find((account) => account.broker.trim().toLowerCase() === params.brokerKey) ?? null;
      params.onSetSelectedAccountId(nextAccount?.id ?? null);
      onNotice("success", "Đã gỡ tài khoản khỏi ứng dụng.");
    } catch (error) {
      onNotice("error", getFriendlyMt5ActionError("delete", error));
    } finally {
      setDeletingAccount(false);
    }
  }, [deleteConfirmAccountId, loadState, onClearNotice, onNotice, setBotTokenEntitlements]);

  return {
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
  };
}
