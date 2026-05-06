"use client";

import { motion } from "framer-motion";
import { Gift } from "lucide-react";
import type { BonusEventItem } from "@/lib/store";

interface BonusHistoryProps {
  events: BonusEventItem[];
}

export default function BonusHistory({ events }: BonusHistoryProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card overflow-hidden"
    >
      <div className="p-4 border-b border-white/10 flex items-center gap-2">
        <Gift className="h-5 w-5 text-cyber-accentGold" />
        <h3 className="font-semibold text-white">Lịch sử thưởng</h3>
      </div>
      <ul
        className="divide-y divide-white/5 max-h-56 overflow-y-auto"
        style={{ contentVisibility: "auto", containIntrinsicSize: "360px" }}
      >
        {events.length === 0 ? (
          <li className="p-6 text-center text-cyber-muted text-sm">
            Chưa có thưởng nào được ghi nhận.
          </li>
        ) : (
          events.map((ev) => (
            <li key={ev.id} className="flex items-start gap-3 px-4 py-3">
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyber-accentGold/15 text-cyber-accentGold">
                <Gift className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-white">{ev.reason}</p>
                <p className="text-xs text-cyber-muted mt-0.5">{ev.created_at}</p>
              </div>
              <span className="shrink-0 text-sm font-semibold text-cyber-accentGold">
                +${ev.amount.toFixed(2)}
              </span>
            </li>
          ))
        )}
      </ul>
    </motion.div>
  );
}
