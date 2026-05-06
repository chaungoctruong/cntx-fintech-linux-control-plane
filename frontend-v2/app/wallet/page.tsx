"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAppStore } from "@/lib/store";
import { fetchWalletInfo, fetchTransactions } from "@/lib/api";
import BottomNav from "@/components/BottomNav";
import PageHeader from "@/components/PageHeader";
import WalletBalanceCard from "@/components/Wallet/WalletBalanceCard";
import DepositSection from "@/components/Wallet/DepositSection";
import WithdrawSection from "@/components/Wallet/WithdrawSection";
import TransactionHistory from "@/components/Wallet/TransactionHistory";

type Tab = "deposit" | "withdraw";

export default function WalletPage() {
  const [tab, setTab] = useState<Tab>("deposit");
  const [loading, setLoading] = useState(true);
  const balance = useAppStore((s) => s.balance);
  const equity = useAppStore((s) => s.equity);
  const depositAddress = useAppStore((s) => s.depositAddress);
  const transactions = useAppStore((s) => s.transactions);
  const setBalance = useAppStore((s) => s.setBalance);
  const setEquity = useAppStore((s) => s.setEquity);
  const setDepositAddress = useAppStore((s) => s.setDepositAddress);
  const setTransactions = useAppStore((s) => s.setTransactions);

  const loadWallet = useCallback(async () => {
    setLoading(true);
    const [walletRes, transactionsRes] = await Promise.allSettled([
      fetchWalletInfo(),
      fetchTransactions(50),
    ]);

    if (walletRes.status === "fulfilled") {
      setBalance(walletRes.value.balance);
      setEquity(walletRes.value.equity);
      setDepositAddress(walletRes.value.deposit_address);
    }

    if (transactionsRes.status === "fulfilled") {
      setTransactions(transactionsRes.value.transactions);
    } else {
      setTransactions([]);
    }

    setLoading(false);
  }, [
    setBalance,
    setDepositAddress,
    setEquity,
    setTransactions,
  ]);

  useEffect(() => {
    void loadWallet();
  }, [loadWallet]);

  return (
    <>
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="min-h-screen min-h-[100dvh] pb-28 pt-6 px-4 app-bg"
      >
        <div className="relative z-10 mx-auto max-w-md space-y-6">
          <PageHeader title="Ví" showBack />

          {loading ? (
            <div className="glass-card p-6 border-cyber-accentGreen/20 space-y-4">
              <div className="skeleton h-4 w-24 rounded-full" />
              <div className="skeleton h-12 w-44 rounded-2xl" />
              <div className="skeleton h-4 w-40 rounded-full" />
            </div>
          ) : (
            <WalletBalanceCard balance={balance} equity={equity} />
          )}

          <div className="relative rounded-[16px] bg-white/5 p-1 border border-white/10">
            <div
              className="absolute top-1 left-1 h-[calc(100%-8px)] w-[calc(50%-4px)] rounded-lg bg-cyber-accentGreen/20 transition-transform duration-300"
              style={{ transform: tab === "withdraw" ? "translateX(100%)" : "translateX(0)" }}
            />
            <div className="relative flex">
              <button
                type="button"
                onClick={() => setTab("deposit")}
                className="flex-1 min-h-tap py-2.5 rounded-lg text-sm font-medium transition-colors z-10"
                style={{ color: tab === "deposit" ? "var(--cyber-accent-green)" : "var(--cyber-muted)" }}
              >
                Nạp
              </button>
              <button
                type="button"
                onClick={() => setTab("withdraw")}
                className="flex-1 min-h-tap py-2.5 rounded-lg text-sm font-medium transition-colors z-10"
                style={{ color: tab === "withdraw" ? "var(--cyber-accent-green)" : "var(--cyber-muted)" }}
              >
                Rút
              </button>
            </div>
          </div>

          <AnimatePresence mode="wait">
            {tab === "deposit" && (
              <motion.div
                key="deposit"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.2 }}
              >
                {loading ? (
                  <div className="glass-card p-5 space-y-3">
                    <div className="skeleton h-5 w-32 rounded-full" />
                    <div className="skeleton h-12 w-full rounded-xl" />
                    <div className="skeleton h-10 w-full rounded-xl" />
                  </div>
                ) : (
                  <DepositSection depositAddress={depositAddress} />
                )}
              </motion.div>
            )}
            {tab === "withdraw" && (
              <motion.div
                key="withdraw"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.2 }}
              >
                {loading ? (
                  <div className="glass-card p-5 space-y-3">
                    <div className="skeleton h-5 w-32 rounded-full" />
                    <div className="skeleton h-12 w-full rounded-xl" />
                    <div className="skeleton h-10 w-full rounded-xl" />
                  </div>
                ) : (
                  <WithdrawSection balance={balance} onSuccess={loadWallet} />
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <TransactionHistory transactions={transactions} loading={loading} />
        </div>
      </motion.main>
      <BottomNav />
    </>
  );
}
