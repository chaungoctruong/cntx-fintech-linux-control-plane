"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, SlidersHorizontal, Wallet } from "lucide-react";

const navItems = [
  { href: "/bot/", label: "Bot", icon: Bot },
  { href: "/bot/control/", label: "Điều khiển", icon: SlidersHorizontal },
  { href: "/wallet/", label: "Ví", icon: Wallet },
] as const;

export const INTERNAL_BOT_NAV_MARKER = "cntx:internal-bot-nav";

function markInternalBotNavigation(href: string) {
  if (typeof window === "undefined" || href !== "/bot/") return;
  try {
    window.sessionStorage.setItem(INTERNAL_BOT_NAV_MARKER, String(Date.now()));
  } catch {
    /* ignore */
  }
}

export default function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 px-4 pb-[env(safe-area-inset-bottom)] pt-2">
      <div className="mx-auto max-w-md rounded-2xl border border-white/10 bg-black/[0.12] shadow-panel-glow">
        <div className="flex items-center gap-1 px-1 py-2">
          {navItems.map(({ href, label, icon: Icon }) => {
            const normalizedPathname = pathname.endsWith("/") ? pathname : `${pathname}/`;
            const isActive =
              href === "/bot/"
                ? normalizedPathname === "/bot/"
                : normalizedPathname === href || normalizedPathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                onClick={() => markInternalBotNavigation(href)}
                className={`flex min-h-tap min-w-0 flex-1 flex-col items-center justify-center gap-1 px-2 py-3 rounded-xl transition-all duration-300 ${
                  isActive
                    ? "text-cyber-accentGreen scale-[1.04] drop-shadow-[0_0_12px_rgba(0,255,156,0.5)]"
                    : "text-cyber-muted hover:text-cyber-text"
                }`}
              >
                <Icon className="h-6 w-6" strokeWidth={isActive ? 2.2 : 1.8} />
                <span className="text-[11px] font-medium sm:text-xs">{label}</span>
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
