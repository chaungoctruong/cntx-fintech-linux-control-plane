/**
 * Telegram WebApp SDK initialization.
 * Safe to call in browser; no-op when not in Telegram context.
 */

export type TelegramWebApp = {
  ready: () => void;
  expand: () => void;
  close: () => void;
  initData: string;
  initDataUnsafe: {
    user?: { id: number; first_name?: string; last_name?: string; username?: string };
  };
  themeParams?: { bg_color?: string; text_color?: string };
  MainButton: { show: () => void; hide: () => void; setText: (t: string) => void };
  BackButton: {
    show: () => void;
    hide: () => void;
    onClick?: (cb: () => void) => void;
    offClick?: (cb: () => void) => void;
  };
};

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

let webApp: TelegramWebApp | null = null;

const TELEGRAM_WEBAPP_WAIT_MS = 2500;
const TELEGRAM_WEBAPP_POLL_MS = 50;

export function getTelegramWebApp(): TelegramWebApp | null {
  if (typeof window === "undefined") return null;
  if (webApp) return webApp;
  webApp = window.Telegram?.WebApp ?? null;
  return webApp;
}

export async function waitForTelegramWebApp(timeoutMs = TELEGRAM_WEBAPP_WAIT_MS): Promise<TelegramWebApp | null> {
  const readyWebApp = getTelegramWebApp();
  if (readyWebApp || typeof window === "undefined" || timeoutMs <= 0) {
    return readyWebApp;
  }

  const startedAt = Date.now();

  return new Promise((resolve) => {
    let timer: number | null = null;

    const done = (value: TelegramWebApp | null) => {
      if (timer != null) {
        window.clearTimeout(timer);
      }
      resolve(value);
    };

    const poll = () => {
      const twa = getTelegramWebApp();
      if (twa) {
        done(twa);
        return;
      }

      if (Date.now() - startedAt >= timeoutMs) {
        done(null);
        return;
      }

      timer = window.setTimeout(poll, TELEGRAM_WEBAPP_POLL_MS);
    };

    poll();
  });
}

export function initTelegramWebApp(): TelegramWebApp | null {
  const twa = getTelegramWebApp();
  if (!twa) return null;
  twa.ready();
  twa.expand();
  return twa;
}

function getTelegramUserId(): number | null {
  const twa = getTelegramWebApp();
  return twa?.initDataUnsafe?.user?.id ?? null;
}

export function getTelegramTenantUserId(): string | null {
  const userId = getTelegramUserId();
  return userId != null ? `telegram:${userId}` : null;
}

export async function waitForTelegramTenantUserId(timeoutMs = TELEGRAM_WEBAPP_WAIT_MS): Promise<string | null> {
  const tenantUserId = getTelegramTenantUserId();
  if (tenantUserId || typeof window === "undefined" || timeoutMs <= 0) {
    return tenantUserId;
  }

  const startedAt = Date.now();

  return new Promise((resolve) => {
    let timer: number | null = null;

    const done = (value: string | null) => {
      if (timer != null) {
        window.clearTimeout(timer);
      }
      resolve(value);
    };

    const poll = () => {
      const currentTenantUserId = getTelegramTenantUserId();
      if (currentTenantUserId) {
        done(currentTenantUserId);
        return;
      }

      if (Date.now() - startedAt >= timeoutMs) {
        done(null);
        return;
      }

      timer = window.setTimeout(poll, TELEGRAM_WEBAPP_POLL_MS);
    };

    poll();
  });
}
