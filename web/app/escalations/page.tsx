"use client";

import { AlertTriangle, PhoneForwarded, ServerOff, ShieldAlert } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { RedFlagBadge } from "@/components/badges/RedFlagBadge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function EscalationsPage() {
  useRequireAuth();

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-escalate" />
              <span className="text-xs font-semibold uppercase tracking-wide text-escalate">
                Safety operations
              </span>
            </div>
            <h1 className="text-2xl font-semibold text-ink">Escalations queue</h1>
            <p className="mt-1 text-sm text-muted">
              Human handoffs raised by red flags, scope conflicts, verifier vetoes, or exhausted
              clarification budgets.
            </p>
          </div>
          <RedFlagBadge />
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <QueueStat label="Waiting" value="—" icon={AlertTriangle} tone="escalate" />
          <QueueStat label="On a call" value="—" icon={PhoneForwarded} tone="clarify" />
          <QueueStat label="Resolved today" value="—" icon={ShieldAlert} tone="answer" />
        </div>

        <Card>
          <CardHeader
            title="Active handoffs"
            subtitle="Newest safety-critical escalation first"
          />
          <CardBody>
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-16 text-center">
              <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-canvas text-muted">
                <ServerOff className="h-6 w-6" />
              </span>
              <h2 className="text-sm font-semibold text-ink">Escalation read API is not available</h2>
              <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
                The backend records telephony handoffs in an in-memory sink, but it does not expose
                a doctor-scoped <code className="rounded bg-canvas px-1.5 py-0.5">GET /escalations</code>{" "}
                endpoint yet. This queue intentionally shows no fabricated patient data.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

function QueueStat({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  icon: typeof ShieldAlert;
  tone: "answer" | "clarify" | "escalate";
}) {
  const tones = {
    answer: "bg-answer-bg text-answer",
    clarify: "bg-clarify-bg text-clarify",
    escalate: "bg-escalate-bg text-escalate",
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
