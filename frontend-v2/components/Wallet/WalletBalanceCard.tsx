"use client";

import { motion } from "framer-motion";

interface WalletBalanceCardProps {
  balance: number;
  equity: number;
}

export default function WalletBalanceCard({ balance, equity }: WalletBalanceCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden glass-card p-6 border-cyber-accentGreen/30 shadow-neon-green-soft"
    >
      <div className="balance-scan-line" aria-hidden />
      <p className="text-cyber-muted text-sm font-medium uppercase tracking-wider">
        Số dư ví
      </p>
      <p className="mt-2 text-4xl font-bold text-white" style={{ textShadow: "0 0 24px rgba(0,255,156,0.35)" }}>
        ${balance.toLocaleString("en-US", { minimumFractionDigits: 2 })}
      </p>
      <p className="mt-1 text-sm text-cyber-muted">
        Tổng tài sản: ${equity.toLocaleString("en-US", { minimumFractionDigits: 2 })} USD
      </p>
    </motion.div>
  );
}
