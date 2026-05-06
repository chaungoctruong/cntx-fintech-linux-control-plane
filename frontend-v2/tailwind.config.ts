import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ["Syne", "system-ui", "sans-serif"],
        sans: ["Outfit", "system-ui", "sans-serif"],
      },
      colors: {
        cyber: {
          bg: "#05070b",
          panel: "#0b1620",
          card: "#0b1620",
          accentRed: "#ff2b2b",
          accentGreen: "#00ff9c",
          text: "#d9e6f2",
          muted: "#8b9cb4",
          hover: "#0f1a28",
          accentGold: "#facc15",
        },
      },
      boxShadow: {
        "neon-red": "0 0 20px rgba(255, 43, 43, 0.5)",
        "neon-green": "0 0 20px rgba(0, 255, 156, 0.45)",
        "neon-green-soft": "0 0 28px rgba(0, 255, 156, 0.2)",
        "neon-gold": "0 0 20px rgba(250, 204, 21, 0.4)",
        "panel-glow": "0 0 40px rgba(0, 255, 156, 0.08), inset 0 1px 0 rgba(255,255,255,0.04)",
      },
      minHeight: {
        tap: "44px",
      },
      borderRadius: {
        "panel": "16px",
      },
    },
  },
  plugins: [],
};

export default config;
