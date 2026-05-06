"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";
import { requestWithdrawal } from "@/lib/api";

/** Basic wallet address validation: 0x + 40 hex, or at least 26 alphanumeric (other chains). */
export function validateWalletAddress(addr: string): string | null {
  const t = addr.trim();
  if (!t) return "Vui lòng nhập địa chỉ ví.";
  if (t.length < 26) return "Địa chỉ ví quá ngắn.";
  if (t.startsWith("0x")) {
    if (t.length !== 42) return "Địa chỉ EVM phải có dạng 0x + 40 ký tự hex.";
    if (!/^0x[0-9a-fA-F]{40}$/.test(t)) return "Địa chỉ hex không hợp lệ.";
  }
  return null;
}

interface WithdrawSectionProps {
  balance: number;
  onSuccess?: () => void;
}

export default function WithdrawSection({ balance, onSuccess }: WithdrawSectionProps) {
  const [amount, setAmount] = useState("");
  const [walletAddress, setWalletAddress] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const setPercent = (p: number) => {
    if (p >= 1) setAmount(String(balance));
    else setAmount(String(Math.max(0, (balance * p)).toFixed(2)));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const num = parseFloat(amount);
    if (Number.isNaN(num) || num <= 0) {
      setError("Số tiền phải lớn hơn 0.");
      return;
    }
    if (num > balance) {
      setError("Số tiền không được vượt quá số dư.");
      return;
    }
    const addrError = validateWalletAddress(walletAddress);
    if (addrError) {
      setError(addrError);
      return;
    }
    setLoading(true);
    try {
      await requestWithdrawal({ amount: num, wallet_address: walletAddress.trim() });
      setAmount("");
      setWalletAddress("");
      onSuccess?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gửi yêu cầu thất bại.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.form
      initial={{ opacity: 0, x: 8 }}
      animate={{ opacity: 1, x: 0 }}
      onSubmit={handleSubmit}
      className="glass-card p-6 space-y-4"
    >
      <p className="text-sm font-medium text-cyber-muted uppercase tracking-wider">
        Rút tiền
      </p>
      <div>
        <label className="block text-xs text-cyber-muted mb-1">Số tiền (USD)</label>
        <input
          type="number"
          step="0.01"
          min="0"
          inputMode="decimal"
          placeholder="0.00"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-full rounded-xl border border-white/10 bg-cyber-bg/80 px-4 py-3 text-base text-white placeholder-cyber-muted focus:border-cyber-accentRed/50 focus:outline-none focus:ring-1 focus:ring-cyber-accentRed/30 min-h-tap"
        />
        <div className="flex gap-2 mt-2 flex-wrap">
          {([0.25, 0.5, 0.75, 1] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPercent(p)}
              className="min-h-tap px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-cyber-muted text-sm font-medium hover:text-cyber-text hover:border-white/20"
            >
              {p === 1 ? "MAX" : `${p * 100}%`}
            </button>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-xs text-cyber-muted mb-1">Địa chỉ ví</label>
        <input
          type="text"
          placeholder="0x..."
          value={walletAddress}
          onChange={(e) => setWalletAddress(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className="w-full rounded-xl border border-white/10 bg-cyber-bg/80 px-4 py-3 text-base text-white placeholder-cyber-muted focus:border-cyber-accentRed/50 focus:outline-none focus:ring-1 focus:ring-cyber-accentRed/30 font-mono min-h-tap"
        />
      </div>
      {error && (
        <p className="text-sm text-cyber-accentRed">{error}</p>
      )}
      <motion.button
        type="submit"
        disabled={loading}
        className="w-full flex items-center justify-center gap-2 min-h-tap py-3 rounded-xl bg-cyber-accentRed/20 border border-cyber-accentRed/50 text-cyber-accentRed font-medium transition-colors hover:bg-cyber-accentRed/30 disabled:opacity-50"
        whileTap={{ scale: 0.97 }}
      >
        <ArrowUpRight className="h-5 w-5" />
        {loading ? "Đang xác minh ví..." : "Gửi yêu cầu"}
      </motion.button>
    </motion.form>
  );
}
