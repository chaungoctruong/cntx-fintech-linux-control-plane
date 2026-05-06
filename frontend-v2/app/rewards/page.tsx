"use client";

import { useEffect, useCallback, useState } from "react";
import { motion } from "framer-motion";
import { Link2, Copy, Users, DollarSign } from "lucide-react";
import { useAppStore } from "@/lib/store";
import { fetchRewardsInfo, fetchLeaderboard, fetchBonusHistory } from "@/lib/api";
import BottomNav from "@/components/BottomNav";
import PageHeader from "@/components/PageHeader";
import Leaderboard from "@/components/Rewards/Leaderboard";
import BonusHistory from "@/components/Rewards/BonusHistory";
import { useCountUp } from "@/hooks/useCountUp";

export default function RewardsPage() {
  const [loading, setLoading] = useState(true);
  const referralLink = useAppStore((s) => s.referralLink);
  const totalReferrals = useAppStore((s) => s.totalReferrals);
  const bonusEarned = useAppStore((s) => s.bonusEarned);
  const leaderboard = useAppStore((s) => s.leaderboard);
  const bonusEvents = useAppStore((s) => s.bonusEvents);
  const setReferralLink = useAppStore((s) => s.setReferralLink);
  const setTotalReferrals = useAppStore((s) => s.setTotalReferrals);
  const setBonusEarned = useAppStore((s) => s.setBonusEarned);
  const setLeaderboard = useAppStore((s) => s.setLeaderboard);
  const setBonusEvents = useAppStore((s) => s.setBonusEvents);
  const setToast = useAppStore((s) => s.setToast);

  const countInvites = useCountUp(totalReferrals, true);
  const countBonus = useCountUp(bonusEarned, true);

  const loadRewards = useCallback(async () => {
    setLoading(true);
    const [rewardsInfoRes, leaderboardRes, bonusHistoryRes] = await Promise.allSettled([
      fetchRewardsInfo(),
      fetchLeaderboard(),
      fetchBonusHistory(50),
    ]);

    if (rewardsInfoRes.status === "fulfilled") {
      setReferralLink(rewardsInfoRes.value.referral_link);
      setTotalReferrals(rewardsInfoRes.value.total_referrals);
      setBonusEarned(rewardsInfoRes.value.total_bonus);
    }

    if (leaderboardRes.status === "fulfilled") {
      setLeaderboard(leaderboardRes.value.leaderboard);
    } else {
      setLeaderboard([]);
    }

    if (bonusHistoryRes.status === "fulfilled") {
      setBonusEvents(bonusHistoryRes.value.events);
    } else {
      setBonusEvents([]);
    }

    setLoading(false);
  }, [
    setReferralLink,
    setTotalReferrals,
    setBonusEarned,
    setLeaderboard,
    setBonusEvents,
  ]);

  useEffect(() => {
    loadRewards();
  }, [loadRewards]);

  const handleCopyLink = useCallback(async () => {
    if (!referralLink) return;
    try {
      await navigator.clipboard.writeText(referralLink);
      setToast("Đã sao chép link mời ✓");
    } catch {
      setToast(null);
    }
  }, [referralLink, setToast]);

  return (
    <>
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="min-h-screen min-h-[100dvh] pb-28 pt-6 px-4 app-bg"
      >
        <div className="relative z-10 mx-auto max-w-md space-y-6">
          <PageHeader title="Phần thưởng" showBack />

          {loading ? (
            <div className="glass-card p-6 border-cyber-accentGold/20 space-y-4">
              <div className="skeleton h-4 w-28 rounded-full" />
              <div className="skeleton h-4 w-full rounded-full" />
              <div className="skeleton h-12 w-full rounded-xl" />
            </div>
          ) : (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-card p-6 border-cyber-accentGold/30 shadow-neon-gold"
            >
              <p className="text-cyber-accentGold text-sm font-medium uppercase tracking-wider flex items-center gap-2">
                <Link2 className="h-4 w-4" />
                Mời bạn bè nhận thưởng
              </p>
              <p className="mt-2 text-xs text-cyber-muted break-all font-mono">
                {referralLink || "—"}
              </p>
              <motion.button
                type="button"
                onClick={handleCopyLink}
                className="mt-4 w-full min-h-tap flex items-center justify-center gap-2 py-3 rounded-xl border border-cyber-accentGold/50 bg-cyber-accentGold/10 text-cyber-accentGold font-medium transition-colors hover:bg-cyber-accentGold/20"
                whileTap={{ scale: 0.97 }}
              >
                <Copy className="h-5 w-5" />
                Sao chép liên kết
              </motion.button>
            </motion.div>
          )}

          {loading ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="glass-card p-4 space-y-3 border-cyber-accentGreen/10">
                  <div className="skeleton h-4 w-24 rounded-full" />
                  <div className="skeleton h-8 w-20 rounded-xl" />
                </div>
                <div className="glass-card p-4 space-y-3 border-cyber-accentGold/10">
                  <div className="skeleton h-4 w-24 rounded-full" />
                  <div className="skeleton h-8 w-24 rounded-xl" />
                </div>
              </div>
              <div className="glass-card p-4 space-y-3">
                <div className="skeleton h-5 w-32 rounded-full" />
                <div className="skeleton h-14 w-full rounded-xl" />
                <div className="skeleton h-14 w-full rounded-xl" />
                <div className="skeleton h-14 w-full rounded-xl" />
              </div>
              <div className="glass-card p-4 space-y-3">
                <div className="skeleton h-5 w-32 rounded-full" />
                <div className="skeleton h-12 w-full rounded-xl" />
                <div className="skeleton h-12 w-full rounded-xl" />
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 }}
                  className="glass-card p-4 border-cyber-accentGreen/20"
                >
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <Users className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">Tổng lượt mời</span>
                  </div>
                  <p className="mt-2 text-2xl font-bold text-white">{Math.round(countInvites)}</p>
                </motion.div>
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.15 }}
                  className="glass-card p-4 border-cyber-accentGold/20"
                >
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <DollarSign className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">Tổng thưởng</span>
                  </div>
                  <p className="mt-2 text-2xl font-bold text-cyber-accentGold">
                    ${countBonus.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </p>
                </motion.div>
              </div>

              <Leaderboard entries={leaderboard} />
              <BonusHistory events={bonusEvents} />
            </>
          )}
        </div>
      </motion.main>
      <BottomNav />
    </>
  );
}
