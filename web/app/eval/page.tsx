"use client";

import { Activity, CheckCircle2, FlaskConical, ShieldCheck, XCircle } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { VerdictPill } from "@/components/badges/VerdictPill";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";
import { EvalCharts } from "./EvalCharts";
import { EVAL_SNAPSHOT } from "./eval-results";

export default function EvalPage() {
  useRequireAuth();
  const { scenarios } = EVAL_SNAPSHOT;
  const passed = scenarios.filter((scenario) => scenario.status === "pass").length;
  const failed = scenarios.filter((scenario) => scenario.status === "fail").length;

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <FlaskConical className="h-5 w-5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wide text-primary">
                Safety evaluation
              </span>
            </div>
            <h1 className="text-2xl font-semibold text-ink">T1–T8 eval dashboard</h1>
            <p className="mt-1 text-sm text-muted">
              Behavioural checks for temporal truth, isolation, grounded answers, and safe routing.
            </p>
          </div>
          <p className="text-xs text-muted">
            Snapshot {EVAL_SNAPSHOT.generatedAt} · {EVAL_SNAPSHOT.source}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Passed" value={passed} icon={CheckCircle2} tone="answer" />
          <Stat label="Failed" value={failed} icon={XCircle} tone="escalate" />
          <Stat label="Coverage" value={`${scenarios.length}/8`} icon={ShieldCheck} tone="primary" />
        </div>

        <Card>
          <CardHeader title="Eval health" subtitle="Latest validated static result snapshot" />
          <CardBody>
            <EvalCharts scenarios={scenarios} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Scenario results"
            subtitle="Expected safety behaviour and the failure each test prevents"
          />
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left text-sm">
              <thead className="border-b border-border bg-canvas">
                <tr>
                  {["Test", "Scenario", "Expected behaviour", "Verdict", "Status", "Failure caught"].map(
                    (heading) => (
                      <th
                        key={heading}
                        className="whitespace-nowrap px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted first:pl-6 last:pr-6"
                      >
                        {heading}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {scenarios.map((scenario) => (
                  <tr key={scenario.id} className="border-b border-border last:border-0">
                    <td className="px-4 py-4 pl-6 font-semibold text-ink">{scenario.id}</td>
                    <td className="min-w-52 px-4 py-4 font-medium text-ink">{scenario.name}</td>
                    <td className="min-w-80 px-4 py-4 leading-6 text-muted">{scenario.expected}</td>
                    <td className="px-4 py-4">
                      {scenario.verdict ? <VerdictPill verdict={scenario.verdict} /> : "—"}
                    </td>
                    <td className="px-4 py-4">
                      <StatusBadge status={scenario.status} />
                    </td>
                    <td className="min-w-64 px-4 py-4 pr-6 leading-6 text-muted">
                      {scenario.catches}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardBody className="flex items-start gap-3">
            <Activity className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
            <p className="text-sm leading-6 text-muted">
              Live reruns are backend-blocked: <code className="rounded bg-canvas px-1.5 py-0.5">
                rerun_offline_eval
              </code>{" "}
              exists as a Python service but has no authenticated HTTP endpoint. Until that contract
              is exposed, this page displays the last test-validated static snapshot permitted by
              the build plan.
            </p>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

function Stat({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string | number;
  icon: typeof ShieldCheck;
  tone: "answer" | "escalate" | "primary";
}) {
  const tones = {
    answer: "bg-answer-bg text-answer",
    escalate: "bg-escalate-bg text-escalate",
    primary: "bg-primary-muted text-primary",
  };

  return (
    <Card>
      <CardBody className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{value}</p>
        </div>
        <span className={`flex h-10 w-10 items-center justify-center rounded-full ${tones[tone]}`}>
          <Icon className="h-5 w-5" />
        </span>
      </CardBody>
    </Card>
  );
}
