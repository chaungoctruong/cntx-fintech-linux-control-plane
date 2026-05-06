"use client";

import { motion } from "framer-motion";
import { useEffect } from "react";

import { useAppStore } from "@/lib/store";
import { fetchWalletInfo } from "@/lib/api";
import CntxMarketScanner from "@/components/CntxMarketScanner";
import BalanceCard from "@/components/BalanceCard";
import BottomNav from "@/components/BottomNav";

export default function DashboardPage() {
  const setBalance = useAppStore((s) => s.setBalance);
  const setUsername = useAppStore((s) => s.setUsername);

  useEffect(() => {
    fetchWalletInfo()
      .then((wallet) => {
        setBalance(wallet.balance);
      })
      .catch(() => {
        setBalance(0);
      });
    setUsername(null);
  }, [setBalance, setUsername]);

  return (
    <>
      <motion.main
        key="dashboard"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="relative min-h-screen min-h-[100dvh] pb-28 pt-7 px-4 sm:pt-8 sm:px-5 app-bg"
      >
        <div className="relative z-10 mx-auto max-w-md space-y-6 sm:space-y-8">
          <header className="brand-hero text-center" aria-label="CNTx labs">
            <h1 className="brand-title">
              <span className="brand-title-main">CNTx</span>
              <span className="brand-title-sub">labs</span>
            </h1>
            <p className="brand-subtitle">
              Software Platform for Trading Operations
            </p>
          </header>

          <BalanceCard />

          <div className="relative min-h-[200px] overflow-hidden rounded-2xl border border-cyber-accentGreen/20 shadow-panel-glow">
            <CntxMarketScanner />
          </div>
        </div>
      </motion.main>
      <BottomNav />
    </>
  );
}
