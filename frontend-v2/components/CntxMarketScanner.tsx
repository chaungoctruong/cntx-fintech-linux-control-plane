"use client";

import { CSSProperties, useState, useEffect } from "react";
import "@/styles/cntx-market-scanner.css";

type MarketSymbol = {
  id: number;
  symbol: string;
  change: string;
  left: number;
  top: number;
  tone: "muted" | "normal" | "dim";
  size: "sm" | "md" | "lg" | "xs";
  delay: string;
};

type CandlePulse = {
  left: number;
  bottom: number;
  body: number;
  wick: number;
  trend: "up" | "down";
  delay: string;
  duration: string;
};

const SYMBOL_POOL = [
  "BTC", "ETH", "XAU", "SOL", "EUR", "USD", "JPY", "GBP", "XRP", "DOGE", "PEPE", "NVDA", 
  "TSLA", "AAPL", "NAS100", "US30", "GOLD", "OIL", "BNB", "ADA", "DOT", "LINK", "MATIC", 
  "AVAX", "OP", "ARB", "LTC", "SHIB", "NEAR", "FTM"
];

const CANDLE_PULSES: CandlePulse[] = [
  { left: 4, bottom: 15, body: 32, wick: 60, trend: "down", delay: "0.1s", duration: "8s" },
  { left: 10, bottom: 25, body: 22, wick: 50, trend: "up", delay: "1.2s", duration: "7s" },
  { left: 16, bottom: 40, body: 35, wick: 70, trend: "down", delay: "0.5s", duration: "9s" },
  { left: 22, bottom: 20, body: 20, wick: 45, trend: "up", delay: "1.9s", duration: "7.5s" },
  { left: 28, bottom: 55, body: 40, wick: 80, trend: "up", delay: "0.8s", duration: "8.2s" },
  { left: 34, bottom: 30, body: 28, wick: 55, trend: "down", delay: "2.3s", duration: "9.5s" },
  { left: 40, bottom: 65, body: 45, wick: 90, trend: "up", delay: "1.5s", duration: "7.8s" },
  { left: 46, bottom: 35, body: 25, wick: 50, trend: "down", delay: "2.7s", duration: "8.6s" },
  { left: 52, bottom: 10, body: 20, wick: 40, trend: "up", delay: "0.3s", duration: "9.2s" },
  { left: 58, bottom: 50, body: 38, wick: 75, trend: "down", delay: "1.8s", duration: "8.4s" },
  { left: 64, bottom: 25, body: 24, wick: 52, trend: "up", delay: "2.1s", duration: "7.6s" },
  { left: 70, bottom: 70, body: 50, wick: 95, trend: "down", delay: "0.9s", duration: "9.8s" },
  { left: 76, bottom: 45, body: 30, wick: 65, trend: "up", delay: "2.5s", duration: "8.9s" },
  { left: 82, bottom: 20, body: 22, wick: 48, trend: "down", delay: "1.4s", duration: "7.2s" },
  { left: 88, bottom: 60, body: 42, wick: 85, trend: "up", delay: "2.9s", duration: "9.1s" },
  { left: 94, bottom: 30, body: 26, wick: 58, trend: "down", delay: "2.0s", duration: "8s" },
];

export default function CntxMarketScanner() {
  const [symbols, setSymbols] = useState<MarketSymbol[]>([]);

  useEffect(() => {
    // Hàm tạo dữ liệu symbol ngẫu nhiên
    const generateRandomSymbols = () => {
      const count = 18; // Hiển thị 18 mã ngẫu nhiên
      const newSymbols: MarketSymbol[] = Array.from({ length: count }).map((_, i) => {
        const symbol = SYMBOL_POOL[Math.floor(Math.random() * SYMBOL_POOL.length)];
        const isPos = Math.random() > 0.4;
        const changeVal = (Math.random() * (isPos ? 8 : 4)).toFixed(2);
        const sizes: ("xs" | "sm" | "md" | "lg")[] = ["xs", "sm", "md", "lg"];
        const tones: ("muted" | "normal" | "dim")[] = ["muted", "normal", "dim"];

        return {
          id: i,
          symbol,
          change: `${isPos ? "+" : "-"}${changeVal}%`,
          left: Math.random() * 85 + 5, // Tránh sát mép
          top: Math.random() * 80 + 10,
          size: sizes[Math.floor(Math.random() * sizes.length)],
          tone: tones[Math.floor(Math.random() * tones.length)],
          delay: `${(Math.random() * 2).toFixed(2)}s`
        };
      });
      setSymbols(newSymbols);
    };

    generateRandomSymbols();
    // Làm mới ngẫu nhiên sau mỗi 15 giây để tạo flow sống động
    const timer = setInterval(generateRandomSymbols, 15000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="sms__container dark-trading-vibe">
      <div className="sms__scanline" />
      
      <div className="sms__atmosphere" aria-hidden="true">
        <div className="sms__storm-cloud sms__storm-cloud--primary" />
        <div className="sms__fog sms__fog--left" />
        <div className="sms__fog sms__fog--right" />
        <div className="sms__drizzle" />
        <div className="sms__glitch-overlay" />
      </div>

      <div className="sms__grid" aria-hidden="true" />
      <div className="sms__horizon" aria-hidden="true" />

      <div className="sms__candles" aria-hidden="true">
        {CANDLE_PULSES.map((candle, index) => (
          <div
            key={`candle-${index}`}
            className={`sms__candle sms__candle--${candle.trend}`}
            style={
              {
                left: `${candle.left}%`,
                bottom: `${candle.bottom}%`,
                animationDelay: candle.delay,
                animationDuration: candle.duration,
                ["--sms-body-height" as string]: `${candle.body}px`,
                ["--sms-wick-height" as string]: `${candle.wick}px`,
              } as CSSProperties
            }
          >
            <span className="sms__candle-wick-glow" />
            <span className="sms__candle-wick" />
            <span className="sms__candle-body" />
          </div>
        ))}
      </div>

      <div className="sms__symbols">
        {symbols.map((item) => (
          <div
            key={`${item.id}-${item.symbol}`}
            className={`sms__symbol sms__symbol--${item.size} sms__symbol--${item.tone}`}
            style={{
              left: `${item.left}%`,
              top: `${item.top}%`,
              animationDelay: item.delay,
            }}
          >
            <div className="sms__symbol-content">
              <span className="sms__symbol-name">{item.symbol}</span>
              <span
                className={`sms__symbol-change ${item.change.startsWith("+") ? "positive" : "negative"}`}
              >
                {item.change}
              </span>
            </div>
            <div className="sms__symbol-glitch" />
          </div>
        ))}
      </div>

      <div className="sms__vignette-deep" aria-hidden="true" />
    </div>
  );
}
