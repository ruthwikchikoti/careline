"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EvalScenario } from "./eval-results";

const COLORS = {
  pass: "#059669",
  fail: "#DC2626",
  "not-run": "#94A3B8",
};

export function EvalCharts({ scenarios }: { scenarios: EvalScenario[] }) {
  const statusData = (["pass", "fail", "not-run"] as const).map((status) => ({
    name: status === "not-run" ? "Not run" : status[0].toUpperCase() + status.slice(1),
    value: scenarios.filter((scenario) => scenario.status === status).length,
    status,
  }));
  const scenarioData = scenarios.map((scenario) => ({
    id: scenario.id,
    passed: scenario.status === "pass" ? 1 : 0,
  }));

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={statusData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88}>
              {statusData.map((entry) => (
                <Cell key={entry.status} fill={COLORS[entry.status]} />
              ))}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={scenarioData} margin={{ top: 10, right: 10, left: -24, bottom: 0 }}>
            <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="id" tickLine={false} axisLine={false} />
            <YAxis domain={[0, 1]} ticks={[0, 1]} tickLine={false} axisLine={false} />
            <Tooltip />
            <Bar dataKey="passed" name="Pass" fill="#059669" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
