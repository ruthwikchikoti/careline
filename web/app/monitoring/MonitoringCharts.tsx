"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Verdict } from "@/lib/api";

// Same hexes as the tailwind verdict tokens (answer / clarify / escalate).
const VERDICT_COLORS: Record<Verdict, string> = {
  answer: "#059669",
  clarify: "#D97706",
  escalate: "#DC2626",
};
const PRIMARY = "#0E7C86";
const GRID = "#E2E8F0";
const AXIS = "#64748B";

export function VerdictMixChart({ counts }: { counts: Record<Verdict, number> }) {
  const data = (["answer", "clarify", "escalate"] as const).map((verdict) => ({
    verdict,
    name: verdict[0].toUpperCase() + verdict.slice(1),
    turns: counts[verdict] ?? 0,
  }));
  return (
    <div
      className="h-56"
      role="img"
      aria-label={`Verdict mix: ${data.map((d) => `${d.name} ${d.turns}`).join(", ")}`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fill: AXIS, fontSize: 12 }} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: AXIS, fontSize: 12 }} />
          <Tooltip cursor={{ fill: "rgba(100,116,139,0.08)" }} />
          <Bar dataKey="turns" name="Turns" radius={[6, 6, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.verdict} fill={VERDICT_COLORS[d.verdict]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export interface TrendPoint {
  t: string;
  p50: number;
  p95: number;
}

/** Client-side trend of the polled snapshots (resets on page reload). */
export function LatencyTrendChart({ points }: { points: TrendPoint[] }) {
  return (
    <div className="h-56" role="img" aria-label="Latency p50 and p95 over the polled snapshots">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tickLine={false} axisLine={false} tick={{ fill: AXIS, fontSize: 11 }} minTickGap={24} />
          <YAxis tickLine={false} axisLine={false} tick={{ fill: AXIS, fontSize: 11 }} unit=" ms" width={70} />
          <Tooltip />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="p50" name="p50 latency" stroke={PRIMARY} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="p95" name="p95 latency" stroke={VERDICT_COLORS.clarify} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
