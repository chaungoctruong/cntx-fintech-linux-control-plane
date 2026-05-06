"use client";

import { useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { getTelegramWebApp } from "@/lib/telegram";

export interface PageHeaderProps {
  title: string;
  showBack?: boolean;
}

export default function PageHeader({ title, showBack = false }: PageHeaderProps) {
  const handleBack = useCallback(() => {
    if (typeof window === "undefined") return;

    window.location.replace("/");
  }, []);

  useEffect(() => {
    const twa = getTelegramWebApp();
    if (!showBack || !twa?.BackButton) return;

    const onBack = () => handleBack();
    twa.BackButton.show();
    twa.BackButton.onClick?.(onBack);

    return () => {
      twa.BackButton.offClick?.(onBack);
      twa.BackButton.hide();
    };
  }, [handleBack, showBack]);

  return (
    <header
      className="flex items-center justify-center relative h-14 min-h-[56px] w-full max-w-md mx-auto rounded-b-[16px] border border-t-0 border-white/10 border-b-cyber-accentGreen/20 bg-black/[0.10] shadow-panel-glow"
      style={{ minHeight: "56px" }}
    >
      {showBack && (
        <motion.button
          type="button"
          onClick={handleBack}
          className="absolute left-3 top-1/2 -translate-y-1/2 flex items-center gap-1.5 min-h-tap min-w-[44px] pl-1 pr-2 -ml-1 rounded-lg text-cyber-muted hover:text-cyber-text cursor-pointer opacity-80 hover:opacity-100 transition-opacity"
          whileHover={{ scale: 0.98 }}
          whileTap={{ scale: 0.96 }}
          aria-label="Quay lại màn hình trước"
        >
          <ArrowLeft className="h-5 w-5 shrink-0" strokeWidth={2} />
          <span className="text-sm font-medium">Quay lại</span>
        </motion.button>
      )}
      <h1 className="font-display font-semibold text-lg sm:text-xl text-cyber-text">
        {title}
      </h1>
    </header>
  );
}
