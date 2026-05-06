"use client";

import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAppStore } from "@/lib/store";

export default function Toast() {
  const toast = useAppStore((s) => s.toast);
  const setToast = useAppStore((s) => s.setToast);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast, setToast]);

  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          transition={{ duration: 0.2 }}
          className="fixed bottom-24 left-4 right-4 z-50 mx-auto max-w-md flex justify-center pointer-events-none"
        >
          <div className="rounded-[16px] border border-cyber-accentGreen/40 bg-cyber-panel/95 backdrop-blur-md px-4 py-3 text-cyber-accentGreen text-sm font-medium shadow-neon-green-soft">
            {toast}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
