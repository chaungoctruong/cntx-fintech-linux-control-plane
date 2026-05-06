"use client";

import { useEffect, useState } from "react";

const DURATION_MS = 1500;

/** Animate number from 0 to target (counting up). */
export function useCountUp(target: number, enabled: boolean): number {
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setValue(target);
      return;
    }
    let start: number | null = null;
    let rafId: number;

    const tick = (now: number) => {
      if (start == null) start = now;
      const t = Math.min((now - start) / DURATION_MS, 1);
      const easeOut = 1 - (1 - t) ** 2;
      setValue(target * easeOut);
      if (t < 1) rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [target, enabled]);

  return value;
}
