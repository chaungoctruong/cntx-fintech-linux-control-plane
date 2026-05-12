"use client";

import { useEffect } from "react";

import Toast from "@/components/Toast";
import PageTransition from "@/components/PageTransition";
import DisclaimerAcknowledgment from "@/components/DisclaimerAcknowledgment";
import { initTelegramWebApp } from "@/lib/telegram";
import { installClientLogger } from "@/lib/clientLogger";

export default function AppShell({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    try {
      installClientLogger({
        release: process.env.NEXT_PUBLIC_RELEASE || undefined,
      });
    } catch {
      /* never let telemetry break the shell */
    }

    let timer: number | null = null;
    let disposed = false;

    const bootTelegramWebApp = () => {
      if (disposed) return;

      const twa = initTelegramWebApp();
      if (twa) {
        try {
          twa.expand();
        } catch {
          /* ignore */
        }
        return;
      }

      timer = window.setTimeout(bootTelegramWebApp, 180);
    };

    bootTelegramWebApp();

    return () => {
      disposed = true;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  return (
    <>
      <PageTransition>{children}</PageTransition>
      <Toast />
      <DisclaimerAcknowledgment />
    </>
  );
}
