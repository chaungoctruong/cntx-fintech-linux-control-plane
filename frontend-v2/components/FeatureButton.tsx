"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import type { LucideIcon } from "lucide-react";

interface FeatureButtonProps {
  title: string;
  desc: string;
  icon: LucideIcon;
  href: string;
  delay?: number;
}

export default function FeatureButton({
  title,
  desc,
  icon: Icon,
  href,
  delay = 0,
}: FeatureButtonProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: reduceMotion ? 0 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0.18 : 0.28, delay }}
    >
      <Link href={href} className="block min-h-tap flex items-center">
        <motion.div
          className="glass-card p-5 w-full border-cyber-accentGreen/20 hover:border-cyber-accentGreen/40 hover:shadow-neon-green-soft transition-all duration-300 active:scale-[0.99]"
          whileHover={reduceMotion ? undefined : { y: -1.5 }}
          whileTap={reduceMotion ? undefined : { scale: 0.985 }}
          style={{ willChange: "transform" }}
        >
          <div className="flex items-center gap-4 min-h-[44px]">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-cyber-accentGreen/15 text-cyber-accentGreen border border-cyber-accentGreen/30">
              <Icon className="h-6 w-6" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-cyber-text">{title}</h3>
              <p className="mt-0.5 text-sm text-cyber-muted">{desc}</p>
            </div>
            <span className="shrink-0 text-cyber-accentGreen text-lg">→</span>
          </div>
        </motion.div>
      </Link>
    </motion.div>
  );
}
