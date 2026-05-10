"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { MINIAPP_DISCLAIMER_NOTICE_PARAGRAPHS } from "@/components/Bot/MiniappTermsModal";

const STORAGE_KEY = "cntx.disclaimer_ack.v1";

function isAcknowledged(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return true;
  }
}

function persistAcknowledgment() {
  try {
    window.localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    /* localStorage may be blocked in private mode — fail open */
  }
}

export default function DisclaimerAcknowledgment() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!isAcknowledged()) {
      setOpen(true);
    }
  }, []);

  if (!open) return null;

  const handleAccept = () => {
    persistAcknowledgment();
    setOpen(false);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="cntx-disclaimer-title"
      className="fixed inset-0 z-[90] flex min-h-[100dvh] items-end justify-center bg-black/70 px-3 py-3 backdrop-blur-md sm:items-center"
    >
      <div className="flex w-full max-w-md flex-col overflow-hidden rounded-t-[28px] border border-amber-300/25 bg-[#0a1320] shadow-2xl shadow-black/40 sm:rounded-[28px]">
        <div className="border-b border-white/10 bg-amber-300/10 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="rounded-2xl border border-amber-300/30 bg-amber-300/15 p-2 text-amber-100">
              <ShieldAlert className="h-5 w-5" strokeWidth={1.9} />
            </div>
            <h2
              id="cntx-disclaimer-title"
              className="text-lg font-semibold text-white"
            >
              Lưu ý
            </h2>
          </div>
        </div>

        <div className="space-y-3 px-5 py-5 text-sm leading-6 text-slate-200">
          {MINIAPP_DISCLAIMER_NOTICE_PARAGRAPHS.map((paragraph, idx) => (
            <p key={idx}>{paragraph}</p>
          ))}
        </div>

        <div className="border-t border-white/10 bg-[#070d16]/95 px-5 py-4">
          <button
            type="button"
            onClick={handleAccept}
            className="flex min-h-[50px] w-full items-center justify-center rounded-2xl border border-cyan-300/30 bg-cyan-300/15 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/45 hover:bg-cyan-300/20"
          >
            Tôi đã đọc và đồng ý
          </button>
        </div>
      </div>
    </div>
  );
}
