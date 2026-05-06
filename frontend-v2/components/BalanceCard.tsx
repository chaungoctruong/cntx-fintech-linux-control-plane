"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useAppStore } from "@/lib/store";

const DURATION_MS = 1200;

/** Animate from 0 to target value over duration (count-up effect). */
function useCountUp(target: number, enabled: boolean) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setDisplay(target);
      return;
    }
    let start: number | null = null;
    let rafId: number;

    const tick = (now: number) => {
      if (start == null) start = now;
      const t = Math.min((now - start) / DURATION_MS, 1);
      const easeOut = 1 - (1 - t) ** 2;
      setDisplay(target * easeOut);
      if (t < 1) rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [target, enabled]);

  return display;
}

export default function BalanceCard() {
  const balance = useAppStore((s) => s.balance);
  const [hasAnimated, setHasAnimated] = useState(false);
  const displayValue = useCountUp(balance, !hasAnimated);

  useEffect(() => {
    const t = setTimeout(() => setHasAnimated(true), DURATION_MS + 200);
    return () => clearTimeout(t);
  }, []);

  return (
    <motion.div
      className="relative overflow-hidden glass-card p-6 w-full border-cyber-accentGreen/30 shadow-neon-green-soft"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
    >
      <div className="balance-scan-line" aria-hidden />
      <p className="text-cyber-muted text-sm font-medium uppercase tracking-wider">
        Số dư
      </p>
      <motion.p
        className="mt-2 text-4xl sm:text-5xl font-bold text-white tracking-tight"
        style={{ textShadow: "0 0 24px rgba(0,255,156,0.35)" }}
        initial={{ opacity: 0.6 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.3 }}
      >
        ${displayValue.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </motion.p>
      <p className="mt-1 text-xs text-cyber-muted">Đơn vị USD</p>
    </motion.div>
  );
}
