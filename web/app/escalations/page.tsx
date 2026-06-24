"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, PhoneForwarded, ServerOff, ShieldAlert } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { RedFlagBadge } from "@/components/badges/RedFlagBadge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";
import { getEscalations, type EscalationsQueue } from "@/lib/api";

export default function EscalationsPage() {
  useRequireAuth();

  const [queue, setQueue] = useState<EscalationsQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getEscalations()
      .then((data) => active && setQueue(data))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const escalations = queue?.escalations ?? [];

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
          <QueueStat
            label="Waiting"
            value={queue ? String(queue.waiting) : "—"}
            icon={AlertTriangle}
            tone="escalate"
          />
          <QueueStat
            label="On a call"
            value={queue ? "0" : "—"}
            icon={PhoneForwarded}
            tone="clarify"
          />
          <QueueStat
            label="Resolved today"
            value={queue ? "0" : "—"}
            icon={ShieldAlert}
            tone="answer"
          />
        </div>

        <Card>
          <CardHeader
            title="Active handoffs"
            subtitle="Newest safety-critical escalation first"
          />
          <CardBody>
            {escalations.length > 0 ? (
              <ul className="space-y-3">
                {escalations.map((turn) => (
                  <li
                    key={turn.turn_id}
                    className="rounded-xl border border-border bg-surface px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-ink">{turn.patient_id}</p>
                      <span className="whitespace-nowrap text-xs text-muted">
                        {new Date(turn.logged_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="mt-1 text-sm leading-6 text-muted">
                      {turn.escalation_reason ?? "Escalated to the doctor."}
                    </p>
                    {turn.question && (
                      <p className="mt-1 text-xs text-muted">
                        Asked: “{turn.question}” · risk {(turn.risk * 100).toFixed(0)}%
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState loading={loading} error={error} />
            )}
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

function EmptyState({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center px-6 py-16 text-sm text-muted">
        Loading escalations…
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-16 text-center">
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-canvas text-muted">
        <ServerOff className="h-6 w-6" />
      </span>
      <h2 className="text-sm font-semibold text-ink">
        {error ? "Could not load escalations" : "No escalations in the queue"}
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
        {error ??
          "When the safety spine routes a turn to ESCALATE — a red flag, scope conflict, or verifier veto — the handoff appears here. None are waiting right now."}
      </p>
    </div>
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
