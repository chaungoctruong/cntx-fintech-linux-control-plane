"use client";

import { useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Copy, Check } from "lucide-react";

interface DepositSectionProps {
  depositAddress: string;
}

export default function DepositSection({ depositAddress }: DepositSectionProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!depositAddress) return;
    try {
      await navigator.clipboard.writeText(depositAddress);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }, [depositAddress]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card p-6 space-y-4"
    >
      <p className="text-sm font-medium text-cyber-muted uppercase tracking-wider">
        Nạp USDT (BEP20)
      </p>
      <div className="flex justify-center">
        <div className="h-40 w-40 rounded-xl border border-white/10 bg-cyber-bg/80 flex items-center justify-center text-cyber-muted text-xs">
          Mã QR
        </div>
      </div>
      <p className="text-xs text-cyber-muted text-center break-all font-mono">
        {depositAddress || "—"}
      </p>
      <motion.button
        type="button"
        onClick={handleCopy}
        className="w-full min-h-tap flex items-center justify-center gap-2 py-3 rounded-xl border border-cyber-accentGreen/50 bg-cyber-accentGreen/10 text-cyber-accentGreen font-medium transition-colors hover:bg-cyber-accentGreen/20"
        whileTap={{ scale: 0.97 }}
      >
        {copied ? (
          <>
            <Check className="h-5 w-5" />
            Đã sao chép!
          </>
        ) : (
          <>
            <Copy className="h-5 w-5" />
            Sao chép địa chỉ
          </>
        )}
      </motion.button>
      <p className="text-xs text-cyber-muted text-center">
        Chỉ gửi USDT (BEP20) đến địa chỉ này.
      </p>
    </motion.div>
  );
}
