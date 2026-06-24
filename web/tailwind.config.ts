import type { Config } from "tailwindcss";

// CareLine design tokens (UI-BUILD-PLAN §4.2). The shared contract every member
// builds against — change only by coordination.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: "#0E7C86", fg: "#FFFFFF", muted: "#E6F3F4" },
        canvas: "#F6F8FA",
        surface: "#FFFFFF",
        ink: "#0F172A",
        muted: "#64748B",
        border: "#E2E8F0",
        // verdict / safety — the only "loud" colors
        answer: { DEFAULT: "#059669", bg: "#ECFDF5" },
        clarify: { DEFAULT: "#D97706", bg: "#FFFBEB" },
        escalate: { DEFAULT: "#DC2626", bg: "#FEF2F2" },
        redflag: "#B91C1C",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      borderRadius: { "2xl": "1rem" },
      boxShadow: {
        soft: "0 1px 2px rgba(15,23,42,0.04), 0 4px 12px rgba(15,23,42,0.06)",
      },
    },
  },
  plugins: [],
};

export default config;
