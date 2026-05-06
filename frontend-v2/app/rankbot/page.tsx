"use client";

import { motion } from "framer-motion";
import { Trophy, Radar, ShieldCheck, Activity } from "lucide-react";
import BottomNav from "@/components/BottomNav";
import PageHeader from "@/components/PageHeader";

const previewCards = [
  {
    title: "Bot có tỷ lệ thắng cao",
    hint: "Dữ liệu trực tiếp sắp có",
    detail: "Nhóm bot có tỷ lệ thắng đã xác minh tốt nhất trong hệ thống.",
    icon: Trophy,
    accent: "text-amber-300 border-amber-300/20 bg-amber-300/10",
  },
  {
    title: "Bot ổn định nhất",
    hint: "Dữ liệu trực tiếp sắp có",
    detail: "Cân bằng tốt nhất giữa hiệu suất, độ đều và độ bền lệnh.",
    icon: ShieldCheck,
    accent: "text-cyan-300 border-cyan-300/20 bg-cyan-300/10",
  },
  {
    title: "Bot bắt nhịp nhanh nhất",
    hint: "Dữ liệu trực tiếp sắp có",
    detail: "Nhóm bot phản ứng nhanh nhất trước các chuyển động mới của thị trường.",
    icon: Activity,
    accent: "text-cyber-accentGreen border-cyber-accentGreen/20 bg-cyber-accentGreen/10",
  },
];

export default function RankBotPage() {
  return (
    <>
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="min-h-screen min-h-[100dvh] pb-28 pt-6 px-4 app-bg"
      >
        <div className="relative z-10 mx-auto max-w-md space-y-6">
          <PageHeader title="Xếp hạng bot" showBack />

          <motion.section
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card overflow-hidden border-cyber-accentGreen/20"
          >
            <div className="border-b border-white/10 bg-[radial-gradient(circle_at_top_right,rgba(0,255,156,0.12),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.02),transparent)] p-5">
              <div className="inline-flex items-center gap-2 rounded-full border border-cyber-accentGreen/20 bg-cyber-accentGreen/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-cyber-accentGreen">
                <Radar className="h-3.5 w-3.5" />
                Bảng xếp hạng sắp lên sóng
              </div>
              <h2 className="mt-4 font-display text-3xl font-bold text-white">
                Theo dõi những bot đang dẫn đầu hiệu suất
              </h2>
              <p className="mt-3 text-sm leading-6 text-cyber-muted">
                Màn hình này đã sẵn sàng cho luồng xếp hạng trực tiếp. Tại đây bạn sẽ thấy những
                nhóm bot mạnh nhất theo tỷ lệ thắng, độ ổn định và chất lượng vận hành thực tế.
              </p>
            </div>

            <div className="grid grid-cols-3 gap-3 p-5">
              <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                <p className="text-[11px] uppercase tracking-[0.2em] text-cyber-muted">Tỷ lệ thắng</p>
                <p className="mt-2 text-2xl font-bold text-white">--</p>
              </div>
              <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                <p className="text-[11px] uppercase tracking-[0.2em] text-cyber-muted">Độ ổn định</p>
                <p className="mt-2 text-2xl font-bold text-white">--</p>
              </div>
              <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                <p className="text-[11px] uppercase tracking-[0.2em] text-cyber-muted">Bot đang chạy</p>
                <p className="mt-2 text-2xl font-bold text-white">--</p>
              </div>
            </div>
          </motion.section>

          <div className="space-y-3">
            {previewCards.map(({ title, hint, detail, icon: Icon, accent }, index) => (
              <motion.section
                key={title}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.05 * index }}
                className="glass-card border-white/10 p-5"
              >
                <div className="flex items-start gap-4">
                  <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border ${accent}`}>
                    <Icon className="h-6 w-6" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold text-white">{title}</h3>
                      <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-cyber-muted">
                        {hint}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-cyber-muted">{detail}</p>
                  </div>
                </div>
              </motion.section>
            ))}
          </div>
        </div>
      </motion.main>
      <BottomNav />
    </>
  );
}
