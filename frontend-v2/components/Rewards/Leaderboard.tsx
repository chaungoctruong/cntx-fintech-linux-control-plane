"use client";

import { motion } from "framer-motion";
import { Trophy } from "lucide-react";
import type { LeaderboardEntry } from "@/lib/store";

interface LeaderboardProps {
  entries: LeaderboardEntry[];
  /** Rank of current user to highlight (e.g. from API). */
  highlightRank?: number;
}

function RankIcon({ rank }: { rank: number }) {
  if (rank === 1) return <span className="text-2xl">🥇</span>;
  if (rank === 2) return <span className="text-2xl">🥈</span>;
  if (rank === 3) return <span className="text-2xl">🥉</span>;
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-sm font-bold text-cyber-muted">
      {rank}
    </span>
  );
}

export default function Leaderboard({ entries, highlightRank }: LeaderboardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card overflow-hidden"
    >
      <div className="p-4 border-b border-white/10 flex items-center gap-2">
        <Trophy className="h-5 w-5 text-cyber-accentGold" />
        <h3 className="font-semibold text-white">Người mời hàng đầu</h3>
      </div>
      <ul
        className="divide-y divide-white/5 max-h-72 overflow-y-auto"
        style={{ contentVisibility: "auto", containIntrinsicSize: "460px" }}
      >
        {entries.length === 0 ? (
          <li className="p-6 text-center text-cyber-muted text-sm">
            Chưa có lượt mời nào. Hãy là người đầu tiên!
          </li>
        ) : (
          entries.map((entry) => (
            <li
              key={`${entry.rank}-${entry.masked_username}`}
              className={`flex items-center gap-3 px-4 py-3 ${
                highlightRank != null && entry.rank === highlightRank
                  ? "bg-cyber-accentGreen/10 border-l-2 border-cyber-accentGreen"
                  : ""
              }`}
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center">
                <RankIcon rank={entry.rank} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-white truncate">
                  {entry.masked_username}
                </p>
                <p className="text-xs text-cyber-muted">Lượt mời</p>
              </div>
              <div className="shrink-0 text-cyber-accentGold font-semibold">
                {entry.referral_count}
              </div>
            </li>
          ))
        )}
      </ul>
    </motion.div>
  );
}
