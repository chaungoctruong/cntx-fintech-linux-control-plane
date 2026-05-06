"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMt5BotTokenEntitlements,
  fetchMt5BotCatalog,
  fetchMt5Dashboard,
  type MT5AccountItem,
  type MT5BotCatalogItem,
  type MT5BotTokenEntitlement,
  type MT5DeploymentItem,
} from "@/lib/api";
import { getFriendlyMt5ActionError } from "@/components/Bot/mt5ControlMessages";

type Snapshot = {
  accounts: MT5AccountItem[];
  bots: MT5BotCatalogItem[];
  deployments: MT5DeploymentItem[];
};

type SnapshotOptions = {
  includeBots?: boolean;
};

type LoadStateOptions = {
  silentErrors?: boolean;
  spinner?: boolean;
  includeBots?: boolean;
};

type LoadEntitlementsOptions = {
  silentErrors?: boolean;
};

type UseMt5BotControlArgs = {
  selectedBroker: string;
  selectedAccountId: number | null;
  onError: (message: string) => void;
};

export function useMt5BotControl({ selectedBroker, selectedAccountId, onError }: UseMt5BotControlArgs) {
  const [accounts, setAccounts] = useState<MT5AccountItem[]>([]);
  const [deployments, setDeployments] = useState<MT5DeploymentItem[]>([]);
  const [bots, setBots] = useState<MT5BotCatalogItem[]>([]);
  const [botCatalogError, setBotCatalogError] = useState<string | null>(null);
  const [loadingState, setLoadingState] = useState(false);
  const [refreshingState, setRefreshingState] = useState(false);
  const [botTokenEntitlements, setBotTokenEntitlements] = useState<MT5BotTokenEntitlement[]>([]);
  const botsRef = useRef<MT5BotCatalogItem[]>([]);

  useEffect(() => {
    botsRef.current = bots;
  }, [bots]);

  const fetchSnapshot = useCallback(async (options?: SnapshotOptions): Promise<Snapshot> => {
    const includeBots = options?.includeBots !== false;
    const dashboard = await fetchMt5Dashboard();
    let nextBots = botsRef.current;
    if (includeBots) {
      try {
        const botCatalog = await fetchMt5BotCatalog();
        nextBots = botCatalog.items;
        setBotCatalogError(null);
      } catch (error) {
        setBotCatalogError(getFriendlyMt5ActionError("load", error));
      }
    }

    return {
      accounts: dashboard.accounts || [],
      deployments: dashboard.deployments || [],
      bots: nextBots,
    };
  }, []);

  const loadState = useCallback(async (options?: LoadStateOptions): Promise<Snapshot | null> => {
    if (options?.spinner !== false) {
      setLoadingState(true);
    }

    try {
      const snapshot = await fetchSnapshot({
        includeBots: options?.includeBots,
      });
      setAccounts(snapshot.accounts);
      setDeployments(snapshot.deployments);
      setBots(snapshot.bots);
      return snapshot;
    } catch (error) {
      if (!options?.silentErrors) {
        onError(getFriendlyMt5ActionError("load", error));
      }
      return null;
    } finally {
      if (options?.spinner !== false) {
        setLoadingState(false);
      }
    }
  }, [fetchSnapshot, onError]);

  const loadBotTokenEntitlements = useCallback(async (
    accountId: number | null,
    options?: LoadEntitlementsOptions
  ) => {
    if (!accountId) {
      setBotTokenEntitlements([]);
      return;
    }
    try {
      const response = await fetchMt5BotTokenEntitlements(accountId);
      setBotTokenEntitlements(response.items);
    } catch (error) {
      if (!options?.silentErrors) {
        onError(getFriendlyMt5ActionError("load", error));
      }
    }
  }, [onError]);

  const refreshState = useCallback(async () => {
    setRefreshingState(true);
    try {
      const snapshot = await loadState({ spinner: false, includeBots: true });
      if (!snapshot) {
        return;
      }
      const brokerKey = selectedBroker.trim().toLowerCase();
      const nextAccount =
        snapshot.accounts.find((account) => account.id === selectedAccountId) ??
        snapshot.accounts.find((account) => account.broker.trim().toLowerCase() === brokerKey) ??
        null;
      await loadBotTokenEntitlements(nextAccount?.id ?? null, { silentErrors: true });
    } finally {
      setRefreshingState(false);
    }
  }, [loadBotTokenEntitlements, loadState, selectedAccountId, selectedBroker]);

  return {
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
  };
}
