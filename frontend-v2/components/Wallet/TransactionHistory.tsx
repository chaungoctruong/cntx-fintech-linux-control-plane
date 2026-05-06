"use client";

import { motion } from "framer-motion";
import { ArrowDownLeft, ArrowUpRight, Clock, CheckCircle, XCircle } from "lucide-react";
import type { TransactionItem } from "@/lib/store";

interface TransactionHistoryProps {
  transactions: TransactionItem[];
  loading?: boolean;
}

function statusLabel(status: string) {
  const s = status?.toLowerCase();
  if (s === "completed") return "Hoàn tất";
  if (s === "rejected" || s === "failed") return "Thất bại";
  return "Đang xử lý";
}

function typeLabel(type: string) {
  const t = type?.toLowerCase();
  if (t === "withdrawal" || t === "out") return "Rút tiền";
  if (t === "deposit" || t === "in") return "Nạp tiền";
  return type || "Giao dịch";
}

function statusIcon(status: string) {
  const s = status?.toLowerCase();
  if (s === "completed") return <CheckCircle className="h-4 w-4 text-cyber-accentGreen" />;
  if (s === "rejected" || s === "failed") return <XCircle className="h-4 w-4 text-cyber-accentRed" />;
  return <Clock className="h-4 w-4 text-cyber-muted" />;
}

function typeIcon(type: string) {
  const t = type?.toLowerCase();
  if (t === "withdrawal" || t === "out") return <ArrowUpRight className="h-4 w-4 text-cyber-accentRed" />;
  return <ArrowDownLeft className="h-4 w-4 text-cyber-accentGreen" />;
}

export default function TransactionHistory({ transactions, loading = false }: TransactionHistoryProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card overflow-hidden"
    >
      <div className="p-4 border-b border-white/10">
        <h3 className="font-semibold text-white">Hoạt động gần đây</h3>
        <p className="text-xs text-cyber-muted mt-0.5">Lịch sử nạp và rút tiền</p>
      </div>
      <ul
        className="divide-y divide-white/5 max-h-64 overflow-y-auto"
        style={{ contentVisibility: "auto", containIntrinsicSize: "420px" }}
      >
        {loading ? (
          Array.from({ length: 4 }).map((_, index) => (
            <li key={`skeleton-${index}`} className="flex items-center gap-3 px-4 py-3">
              <div className="skeleton h-9 w-9 rounded-lg shrink-0" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="skeleton h-3.5 w-24 rounded-full" />
                <div className="skeleton h-3 w-32 rounded-full" />
              </div>
              <div className="skeleton h-4 w-16 rounded-full shrink-0" />
            </li>
          ))
        ) : transactions.length === 0 ? (
          <li className="p-6 text-center text-cyber-muted text-sm">
            Chưa có giao dịch nào.
          </li>
        ) : (
          transactions.map((tx) => (
            <li
              key={String(tx.id)}
              className="flex items-center gap-3 px-4 py-3"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/5">
                {typeIcon(tx.type)}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-white capitalize">{typeLabel(tx.type)}</p>
                <p className="text-xs text-cyber-muted truncate">{tx.created_at}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span
                  className={
                    tx.type?.toLowerCase() === "withdrawal" || tx.type === "out"
                      ? "text-cyber-accentRed"
                      : "text-cyber-accentGreen"
                  }
                >
                  {tx.type?.toLowerCase() === "withdrawal" || tx.type === "out" ? "-" : "+"}
                  ${Math.abs(tx.amount).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                </span>
                {statusIcon(tx.status)}
              </div>
              <span className="hidden">{statusLabel(tx.status)}</span>
            </li>
          ))
        )}
      </ul>
    </motion.div>
  );
}
