import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#f6f7fb",
        ink: {
          950: "#070b16",
          900: "#0b1020",
          800: "#141b34",
          700: "#1e2745",
        },
        aurora: {
          400: "#e8cf8f",
          500: "#d4af37",
          600: "#9a7212",
        },
        electric: {
          400: "#a5b4fc",
          500: "#6366f1",
          600: "#4f46e5",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "Noto Sans SC",
          "Microsoft YaHei",
          "PingFang SC",
          "system-ui",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 18px 50px -32px rgba(7, 11, 22, 0.4)",
        glow: "0 0 0 1px rgba(99,102,241,0.22), 0 18px 50px -28px rgba(99,102,241,0.5)",
      },
      backgroundImage: {
        "aurora-grid":
          "radial-gradient(circle at 15% 10%, rgba(99,102,241,0.16), transparent 32%), radial-gradient(circle at 85% 0%, rgba(212,175,55,0.13), transparent 30%), linear-gradient(rgba(148,163,184,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,0.08) 1px, transparent 1px)",
      },
      backgroundSize: {
        grid: "auto, auto, 32px 32px, 32px 32px",
      },
    },
  },
  plugins: [],
};

export default config;
