"use client";

import { useEffect, useState } from "react";
import { Bot, ServerCog } from "lucide-react";

import BottomNav from "@/components/BottomNav";
import MiniappTermsModal from "@/components/Bot/MiniappTermsModal";
import Mt5BotControlPanel from "@/components/Bot/Mt5BotControlPanel";
import PageHeader from "@/components/PageHeader";
import { useMiniappTerms } from "@/hooks/useMiniappTerms";
import { readStoredMt5Broker, writeStoredMt5Broker } from "@/lib/mt5-preferences";

const mt5BrokerPresets = ["Exness", "XM", "Vantage", "DBG Markets"] as const;

function normalizeBrokerName(value?: string | null): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function brokerNamesMatch(left?: string | null, right?: string | null): boolean {
  const leftValue = normalizeBrokerName(left);
  const rightValue = normalizeBrokerName(right);
  return Boolean(leftValue) && Boolean(rightValue) && leftValue === rightValue;
}

function resolveStoredBroker(): string {
  const storedBroker = readStoredMt5Broker();
  const matchedBroker = mt5BrokerPresets.find(
    (broker) => brokerNamesMatch(broker, storedBroker)
  );
  return matchedBroker ?? mt5BrokerPresets[0];
}

export default function BotControlPage() {
  const [selectedBroker, setSelectedBroker] = useState<string>(mt5BrokerPresets[0]);
  const {
    termsVersion,
    termsModalOpen,
    termsAccepting,
    termsError,
    termsEnabled,
    requireTerms,
    acceptTerms,
  } = useMiniappTerms();

  useEffect(() => {
    setSelectedBroker(resolveStoredBroker());
  }, []);

  function handleBrokerChange(broker: string) {
    setSelectedBroker(broker);
    writeStoredMt5Broker(broker);
  }

  return (
    <>
      <main className="min-h-screen min-h-[100dvh] px-4 pb-28 pt-6 app-bg">
        <div className="relative z-10 mx-auto flex max-w-md flex-col gap-5">
          <PageHeader title="Điều khiển bot" showBack />

          <section className="rounded-3xl border border-cyan-300/15 bg-transparent p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-100">
                  Điều khiển bot
                </p>
                <h3 className="mt-2 font-display text-2xl font-semibold text-white">
                  Bật/tắt bot
                </h3>
                <p className="mt-2 text-sm leading-6 text-cyber-muted">
                  Chọn broker đã đăng nhập rồi điều khiển bot đang gắn với account của bạn.
                </p>
              </div>
              <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-3 text-cyan-100">
                <Bot className="h-5 w-5" strokeWidth={1.9} />
              </div>
            </div>
          </section>

          <section className="rounded-3xl border border-white/10 bg-transparent p-4">
            <div className="flex items-center gap-2 text-cyan-100">
              <ServerCog className="h-4 w-4" strokeWidth={1.9} />
              <p className="text-sm font-semibold uppercase tracking-[0.18em]">Broker</p>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2">
              {mt5BrokerPresets.map((broker) => {
                const isActive = selectedBroker.toLowerCase() === broker.toLowerCase();

                return (
                  <button
                    key={broker}
                    type="button"
                    onClick={() => handleBrokerChange(broker)}
                    className={`min-h-[44px] rounded-2xl border px-3 py-2 text-center text-sm font-semibold transition ${
                      isActive
                        ? "border-cyan-300/35 bg-cyan-300/10 text-white"
                        : "border-white/10 bg-black/[0.08] text-cyber-muted"
                    }`}
                  >
                    {broker}
                  </button>
                );
              })}
            </div>
          </section>

          <Mt5BotControlPanel
            selectedBroker={selectedBroker}
            onRequireTerms={requireTerms}
            termsEnabled={termsEnabled}
          />
        </div>
      </main>

      <MiniappTermsModal
        open={termsModalOpen}
        version={termsVersion}
        accepting={termsAccepting}
        error={termsError}
        onAccept={acceptTerms}
      />
      <BottomNav />
    </>
  );
}
