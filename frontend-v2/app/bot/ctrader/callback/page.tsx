"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { CheckCircle2, ChevronRight, Loader2, ShieldAlert } from "lucide-react";

import BottomNav from "@/components/BottomNav";
import PageHeader from "@/components/PageHeader";
import {
  completeCTraderMiniappOAuthCallback,
  completeCTraderPublicOAuthCallback,
  type CTraderTradingAccount,
} from "@/lib/api";

type StatusTone = "loading" | "success" | "error";

type CallbackState = {
  tone: StatusTone;
  title: string;
  message: string;
  accounts: CTraderTradingAccount[];
  brokerName: string;
  nextAction?: string | null;
  preferredAccountId?: string | null;
  preferredEnvironment?: string | null;
};

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    const detail = error.message.trim();
    if (detail === "account_discovery_in_progress") {
      return "Kết nối đã được lưu nhưng đồng bộ tài khoản vẫn đang chạy. Hãy quay lại trang bot và bấm đồng bộ lại sau ít giây.";
    }
    if (detail === "ctrader_token_rate_limited") {
      return "cTrader đang giới hạn tần suất xác thực. Hãy đợi một chút rồi kết nối lại.";
    }
    if (detail === "ctrader_backend_timeout" || detail === "ctrader_service_timeout") {
      return "Dịch vụ cTrader đang phản hồi chậm. Hãy thử lại sau ít phút.";
    }
    if (detail === "ctrader_service_unavailable") {
      return "Dịch vụ cTrader đang tạm gián đoạn. Hãy thử kết nối lại sau ít phút.";
    }
    if (detail.includes("OAuth callback state could not be validated")) {
      return "Phiên callback cTrader đã hết hạn hoặc không còn hợp lệ. Hãy quay lại trang bot và kết nối lại.";
    }
    return detail;
  }
  return "Hiện chưa thể hoàn tất kết nối cTrader.";
}

function shouldFallbackToPublicCallback(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const detail = error.message.trim();
  return (
    detail === "Missing or invalid Authorization (expected: tma <initData>)" ||
    detail === "Empty initData" ||
    detail === "Invalid initData signature" ||
    detail === "User not found in initData" ||
    detail === "Invalid user id in initData" ||
    detail === "Server auth not configured"
  );
}

function buildInitialState(): CallbackState {
  return {
    tone: "loading",
    title: "Đang hoàn tất kết nối",
    message: "Mini app đang xác thực cTrader, lưu connection và đồng bộ tài khoản giao dịch.",
    accounts: [],
    brokerName: "cTrader",
    nextAction: null,
    preferredAccountId: null,
    preferredEnvironment: null,
  };
}

function normalizeBrokerParam(value?: string | null): string {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
  if (!normalized) {
    return "IC Markets";
  }
  if (normalized === "icmarkets") {
    return "IC Markets";
  }
  return value?.trim() || "IC Markets";
}

function formatBrokerLabel(value?: string | null): string {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
  if (normalized === "icmarkets") {
    return "IC Markets";
  }
  return (value || "").trim() || "cTrader";
}

export default function CTraderCallbackPage() {
  const [result, setResult] = useState<CallbackState>(buildInitialState);
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) {
      return;
    }
    startedRef.current = true;

    let cancelled = false;
    const searchParams = new URLSearchParams(
      typeof window !== "undefined" ? window.location.search : ""
    );
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const error = searchParams.get("error");
    const errorDescription = searchParams.get("error_description");

    async function run() {
      if (error) {
        if (!cancelled) {
          setResult({
            tone: "error",
            title: "cTrader từ chối kết nối",
            message: errorDescription || error,
            accounts: [],
            brokerName: "cTrader",
          });
        }
        return;
      }

      if (!code) {
        if (!cancelled) {
          setResult({
            tone: "error",
            title: "Thiếu mã OAuth",
            message: "Không nhận được mã callback từ cTrader. Hãy quay lại trang bot và kết nối lại.",
            accounts: [],
            brokerName: "cTrader",
          });
        }
        return;
      }

      try {
        let callback;
        try {
          callback = await completeCTraderMiniappOAuthCallback({
            code,
            state,
            scope: "trading",
          });
        } catch (error) {
          if (!shouldFallbackToPublicCallback(error)) {
            throw error;
          }
          callback = await completeCTraderPublicOAuthCallback({
            code,
            state,
            scope: "trading",
          });
        }

        if (cancelled) {
          return;
        }

        const brokerName =
          (typeof callback.client_state === "string" && callback.client_state.trim()) ||
          callback.accounts.find((account) => account.broker_name)?.broker_name ||
          "cTrader";
        const discoverError =
          typeof callback.discover_error === "string" && callback.discover_error.trim()
            ? callback.discover_error.trim()
            : null;
        const defaultSelection =
          callback.default_account_selection &&
          typeof callback.default_account_selection === "object" &&
          !Array.isArray(callback.default_account_selection)
            ? callback.default_account_selection
            : null;
        const preferredAccountId =
          typeof defaultSelection?.trading_account_id === "string" && defaultSelection.trading_account_id.trim()
            ? defaultSelection.trading_account_id.trim()
            : callback.accounts.length === 1
              ? callback.accounts[0]?.id || null
              : null;
        const preferredEnvironment =
          typeof defaultSelection?.environment === "string" && defaultSelection.environment.trim()
            ? defaultSelection.environment.trim()
            : callback.accounts.length === 1
              ? callback.accounts[0]?.environment || null
              : null;

          setResult({
            tone: "success",
            title: "Đã kết nối cTrader",
            message:
              discoverError != null
                ? `Đã lưu kết nối ${formatBrokerLabel(
                    brokerName
                  )}, nhưng danh sách tài khoản vẫn đang đồng bộ. Mini App sẽ quay lại trang bot để bạn tiếp tục làm mới.`
                : callback.next_action === "ready"
                  ? `Đã kết nối ${formatBrokerLabel(
                      brokerName
                    )} và khôi phục tài khoản mặc định đã lưu. Mini App sẽ quay lại trang bot để bạn tiếp tục ngay.`
                  : callback.next_action === "confirm_live_risk"
                    ? `Đã kết nối ${formatBrokerLabel(
                        brokerName
                      )}. Mini App sẽ quay lại trang bot để bạn xác nhận rủi ro cho tài khoản live trước khi tiếp tục.`
                  : callback.accounts.length > 0
                    ? `Đã kết nối ${formatBrokerLabel(brokerName)} và đồng bộ ${callback.accounts.length} tài khoản giao dịch.`
                    : "Đã lưu kết nối cTrader nhưng chưa thấy tài khoản giao dịch nào để đồng bộ.",
            accounts: callback.accounts,
            brokerName: formatBrokerLabel(brokerName),
            nextAction: callback.next_action || null,
            preferredAccountId,
            preferredEnvironment,
          });
      } catch (error) {
        if (!cancelled) {
          setResult({
            tone: "error",
            title: "Kết nối chưa hoàn tất",
            message: getErrorMessage(error),
            accounts: [],
            brokerName: "cTrader",
          });
        }
      }
    }

    void run();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (result.tone !== "success") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      const nextUrl = new URL("/bot/", window.location.origin);
      nextUrl.searchParams.set("broker", normalizeBrokerParam(result.brokerName));
      nextUrl.searchParams.set("lane", "ctrader");
      nextUrl.searchParams.set("connected", "1");
      nextUrl.searchParams.set("accounts", String(result.accounts.length));
      if (result.nextAction) {
        nextUrl.searchParams.set("next_action", result.nextAction);
      }
      if (result.preferredAccountId) {
        nextUrl.searchParams.set("trading_account_id", result.preferredAccountId);
      }
      if (result.preferredEnvironment) {
        nextUrl.searchParams.set("account_env", result.preferredEnvironment);
      }
      window.location.replace(nextUrl.toString());
    }, 1800);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [result]);

  const cardToneClass =
    result.tone === "success"
      ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-100"
      : result.tone === "error"
        ? "border-rose-400/25 bg-rose-400/10 text-rose-100"
        : "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";

  const Icon = result.tone === "success" ? CheckCircle2 : result.tone === "error" ? ShieldAlert : Loader2;

  return (
    <>
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="min-h-screen min-h-[100dvh] px-4 pb-28 pt-6 app-bg"
      >
        <div className="relative z-10 mx-auto flex max-w-md flex-col gap-5">
          <PageHeader title="Callback cTrader" showBack />

          <section className="glass-card border border-cyan-300/15 p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-100">
                  {result.brokerName} cTrader
                </p>
                <h3 className="mt-2 font-display text-2xl font-semibold text-white">
                  {result.title}
                </h3>
                <p className="mt-2 text-sm leading-6 text-cyber-muted">
                  {result.message}
                </p>
              </div>
              <div className={`rounded-2xl border p-3 ${cardToneClass}`}>
                <Icon className={`h-5 w-5 ${result.tone === "loading" ? "animate-spin" : ""}`} strokeWidth={1.9} />
              </div>
            </div>

            {result.accounts.length > 0 && (
              <div className="mt-5 space-y-3">
                {result.accounts.map((account) => (
                  <div
                    key={account.id}
                    className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3 text-sm text-cyber-muted"
                  >
                    <p className="font-semibold text-white">
                      {account.broker_name || "cTrader account"} · {account.environment.toUpperCase()}
                    </p>
                    <p className="mt-1">Account ID: {account.external_account_id}</p>
                    <p className="mt-1">
                      Base currency: {account.base_currency || "N/A"} · Leverage: {account.leverage || "N/A"}
                    </p>
                  </div>
                ))}
              </div>
            )}

            <div className={`mt-5 rounded-2xl border px-4 py-3 text-sm leading-6 ${cardToneClass}`}>
              {result.tone === "loading"
                ? "Trình duyệt sẽ ở lại trang này cho đến khi callback và account discovery hoàn tất."
                : result.tone === "success"
                  ? "Mini app sẽ tự quay lại trang bot trong giây lát để hiển thị lane cTrader đã đồng bộ xong."
                  : "Hãy quay lại trang bot và thử kết nối lại. Nếu lỗi lặp lại, hãy đợi ít phút rồi thử lại."}
            </div>

            <button
              type="button"
              onClick={() => {
                if (typeof window !== "undefined") {
                  window.location.replace("/bot/");
                }
              }}
              className="mt-5 flex min-h-[52px] w-full items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/15 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/45 hover:bg-cyan-300/20"
            >
              Quay lại trang bot
              <ChevronRight className="h-4 w-4" strokeWidth={1.9} />
            </button>
          </section>
        </div>
      </motion.main>

      <BottomNav />
    </>
  );
}
