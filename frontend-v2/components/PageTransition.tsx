"use client";

import { usePathname } from "next/navigation";
import { useRef } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

const ROUTES_ORDER = ["/", "/bot/", "/bot/control/", "/wallet/", "/rewards/"];

function routeIndex(path: string): number {
  const normalized = path.endsWith("/") ? path : `${path}/`;
  const i = ROUTES_ORDER.findIndex((r) => normalized === r || normalized.startsWith(r));
  return i >= 0 ? i : 0;
}

export default function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const prevPathRef = useRef(pathname);
  const reduceMotion = useReducedMotion();

  const prevIdx = routeIndex(prevPathRef.current);
  const currIdx = routeIndex(pathname);
  
  // 1 = next, -1 = prev, 0 = same/unknown
  const direction = currIdx > prevIdx ? 1 : currIdx < prevIdx ? -1 : 0;
  
  if (prevPathRef.current !== pathname) {
    prevPathRef.current = pathname;
  }

  // SaaS Standard: Slightly less movement for better focus
  const xOffset = reduceMotion ? 0 : direction === 1 ? 16 : direction === -1 ? -16 : 0;

  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <motion.div
        key={pathname}
        initial={{ opacity: 0, x: xOffset }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: -xOffset }}
        transition={{
          duration: 0.25,
          ease: [0.32, 0.72, 0, 1], // Custom Cubic-Bezier for smoothness
        }}
        className="w-full"
        style={{ willChange: "transform, opacity" }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
