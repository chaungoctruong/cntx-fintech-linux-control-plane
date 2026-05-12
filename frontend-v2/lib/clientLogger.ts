/**
 * Client-side error reporter for the Mini App.
 *
 * Hooks `window.onerror` and `window.onunhandledrejection`, batches the events
 * locally, and POSTs them to `/api/v2/system/client-events` on the backend.
 * Designed to be defensive — if the backend is down, batching keeps trying with
 * exponential backoff but never throws into the page.
 *
 * Importing this module from a client component idempotently installs the hooks
 * once per browser session.
 */

type Severity = "info" | "warning" | "error" | "fatal";

interface ClientEventPayload {
  type: string;
  severity?: Severity;
  message: string;
  stack?: string;
  occurred_at?: number;
  page_url?: string;
  user_agent?: string;
  extra?: Record<string, unknown>;
}

type StoredEvent = ClientEventPayload & {
  occurred_at: number;
  page_url: string;
  user_agent: string;
};

interface LoggerOptions {
  endpoint?: string;
  flushIntervalMs?: number;
  maxBatchSize?: number;
  maxQueueSize?: number;
  release?: string;
}

const DEFAULT_FLUSH_INTERVAL_MS = 4000;
const DEFAULT_MAX_BATCH_SIZE = 25;
const DEFAULT_MAX_QUEUE_SIZE = 200;
const SESSION_STORAGE_KEY = "cntx_client_session_id";
const HOOK_INSTALLED_FLAG = "__cntxClientLoggerInstalled";

let queue: StoredEvent[] = [];
let flushTimer: number | null = null;
let backoffMultiplier = 1;
let lastFlushAt = 0;
let installedOptions: Required<Omit<LoggerOptions, "release">> & { release: string } = {
  endpoint: "/api/v2/system/client-events",
  flushIntervalMs: DEFAULT_FLUSH_INTERVAL_MS,
  maxBatchSize: DEFAULT_MAX_BATCH_SIZE,
  maxQueueSize: DEFAULT_MAX_QUEUE_SIZE,
  release: "",
};

function safeWindow(): (Window & typeof globalThis) | null {
  return typeof window !== "undefined" ? window : null;
}

function getSessionId(): string {
  const w = safeWindow();
  if (!w || !w.sessionStorage) return "";
  try {
    let id = w.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
      id = `c-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
      w.sessionStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

function resolveEndpoint(): string {
  const path = installedOptions.endpoint;
  const w = safeWindow();
  if (!w) return path;
  // Same-origin assumption: backend serves the Mini App, so a relative path is enough.
  if (path.startsWith("http")) return path;
  try {
    return new URL(path, w.location.origin).toString();
  } catch {
    return path;
  }
}

function scheduleFlush(): void {
  const w = safeWindow();
  if (!w) return;
  if (flushTimer != null) return;
  const delay = installedOptions.flushIntervalMs * backoffMultiplier;
  flushTimer = w.setTimeout(() => {
    flushTimer = null;
    void flush();
  }, delay);
}

async function flush(force = false): Promise<void> {
  const w = safeWindow();
  if (!w) return;
  if (queue.length === 0) return;

  const now = Date.now();
  if (!force && now - lastFlushAt < installedOptions.flushIntervalMs) {
    scheduleFlush();
    return;
  }

  const batch = queue.splice(0, installedOptions.maxBatchSize);
  const body = {
    session_id: getSessionId(),
    release: installedOptions.release || undefined,
    events: batch,
  };

  lastFlushAt = now;
  try {
    const res = await fetch(resolveEndpoint(), {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`status_${res.status}`);
    backoffMultiplier = 1;
  } catch {
    // Re-queue at the front, cap to avoid unbounded growth.
    backoffMultiplier = Math.min(backoffMultiplier * 2, 8);
    const remaining = installedOptions.maxQueueSize - queue.length;
    if (remaining > 0) {
      queue = [...batch.slice(-remaining), ...queue];
    }
  }

  if (queue.length > 0) {
    scheduleFlush();
  }
}

function enqueue(event: ClientEventPayload): void {
  const w = safeWindow();
  if (!w) return;
  if (queue.length >= installedOptions.maxQueueSize) {
    queue.shift();
  }
  const stored: StoredEvent = {
    type: event.type || "error",
    severity: event.severity || "error",
    message: (event.message || "").slice(0, 4000),
    stack: event.stack ? event.stack.slice(0, 8000) : undefined,
    occurred_at: event.occurred_at || Date.now(),
    page_url: event.page_url || w.location.href,
    user_agent: event.user_agent || w.navigator.userAgent,
    extra: event.extra,
  };
  queue.push(stored);
  scheduleFlush();
}

export function reportClientEvent(event: ClientEventPayload): void {
  enqueue(event);
}

export function installClientLogger(options: LoggerOptions = {}): void {
  const w = safeWindow();
  if (!w) return;
  const flagged = w as unknown as { [HOOK_INSTALLED_FLAG]?: boolean };
  if (flagged[HOOK_INSTALLED_FLAG]) {
    if (options.release) installedOptions.release = options.release;
    return;
  }
  flagged[HOOK_INSTALLED_FLAG] = true;

  installedOptions = {
    endpoint: options.endpoint || installedOptions.endpoint,
    flushIntervalMs: options.flushIntervalMs ?? installedOptions.flushIntervalMs,
    maxBatchSize: options.maxBatchSize ?? installedOptions.maxBatchSize,
    maxQueueSize: options.maxQueueSize ?? installedOptions.maxQueueSize,
    release: options.release || installedOptions.release,
  };

  w.addEventListener("error", (event: ErrorEvent) => {
    enqueue({
      type: "window.error",
      severity: "error",
      message: event.message || (event.error && (event.error as Error).message) || "unknown_error",
      stack: event.error instanceof Error ? event.error.stack : undefined,
      extra: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      },
    });
  });

  w.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => {
    const reason = event.reason;
    let message = "unhandled_rejection";
    let stack: string | undefined;
    if (reason instanceof Error) {
      message = reason.message || message;
      stack = reason.stack;
    } else if (typeof reason === "string") {
      message = reason;
    } else if (reason && typeof reason === "object") {
      try {
        message = JSON.stringify(reason).slice(0, 2000);
      } catch {
        message = String(reason);
      }
    }
    enqueue({
      type: "unhandled_rejection",
      severity: "error",
      message,
      stack,
    });
  });

  // Best-effort flush on page hide so trailing events are sent before navigation.
  w.addEventListener("pagehide", () => {
    if (queue.length === 0) return;
    try {
      const blob = new Blob(
        [
          JSON.stringify({
            session_id: getSessionId(),
            release: installedOptions.release || undefined,
            events: queue.splice(0, installedOptions.maxQueueSize),
          }),
        ],
        { type: "application/json" },
      );
      // sendBeacon is fire-and-forget and tolerated during pagehide.
      if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
        navigator.sendBeacon(resolveEndpoint(), blob);
      }
    } catch {
      // ignored
    }
  });
}
