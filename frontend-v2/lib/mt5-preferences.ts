const MT5_LAST_BROKER_KEY = "cntx_mt5_last_broker";

export function readStoredMt5Broker(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const value = window.localStorage.getItem(MT5_LAST_BROKER_KEY)?.trim();
  return value || null;
}

export function writeStoredMt5Broker(broker: string): void {
  if (typeof window === "undefined") {
    return;
  }

  const value = broker.trim();
  if (!value) {
    return;
  }

  window.localStorage.setItem(MT5_LAST_BROKER_KEY, value);
}
