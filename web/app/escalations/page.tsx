"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  PhoneForwarded,
  Send,
  ServerOff,
  ShieldAlert,
} from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { RedFlagBadge } from "@/components/badges/RedFlagBadge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";
import { getEscalations, resolveEscalation, type AuditTurn, type EscalationsQueue } from "@/lib/api";

export default function EscalationsPage() {
  useRequireAuth();

  const [queue, setQueue] = useState<EscalationsQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    return getEscalations()
      .then(setQueue)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = queue?.groups ?? [];

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
            label="Patients waiting"
            value={queue ? String(queue.patients_waiting) : "—"}
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
            subtitle="Grouped by patient · most recently escalated first"
          />
          <CardBody>
            {groups.length > 0 ? (
              <div className="space-y-5">
                {groups.map((group) => (
                  <section key={group.patient_id}>
                    {/* patient header */}
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-escalate-bg text-escalate">
                          <ShieldAlert className="h-4 w-4" />
                        </span>
                        <p className="text-sm font-semibold text-ink">{group.patient_id}</p>
                        <span className="rounded-full bg-escalate-bg px-2 py-0.5 text-xs font-medium text-escalate">
                          {group.count} waiting
                        </span>
                      </div>
                      <span className="whitespace-nowrap text-xs text-muted">
                        latest {new Date(group.latest_at).toLocaleString()}
                      </span>
                    </div>
                    {/* this patient's escalated turns */}
                    <ul className="space-y-2 border-l-2 border-escalate-bg pl-3">
                      {group.escalations.map((turn) => (
                        <li key={turn.turn_id}>
                          <EscalationItem turn={turn} onResolved={load} />
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            ) : (
              <EmptyState loading={loading} error={error} />
            )}
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

function EscalationItem({ turn, onResolved }: { turn: AuditTurn; onResolved: () => void }) {
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function send() {
    const text = reply.trim();
    if (!text || sending) return;
    setSending(true);
    setErr(null);
    try {
      await resolveEscalation(turn.turn_id, text);
      onResolved(); // refresh the queue → this turn now shows as answered
    } catch (e) {
      setErr(String(e));
      setSending(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm leading-6 text-ink">
          {turn.escalation_reason ?? "Escalated to the doctor."}
        </p>
        <span className="whitespace-nowrap text-xs text-muted">
          {new Date(turn.logged_at).toLocaleTimeString()}
        </span>
      </div>
      {turn.question && (
        <p className="mt-1 text-xs text-muted">
          Asked: “{turn.question}” · risk {(turn.risk * 100).toFixed(0)}%
        </p>
      )}

      {turn.resolved ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-answer-bg px-3 py-2">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-answer" />
          <div>
            <p className="text-xs font-medium text-answer">
              You replied{turn.resolved_at ? ` · ${new Date(turn.resolved_at).toLocaleString()}` : ""}
            </p>
            <p className="text-sm leading-6 text-ink">{turn.reply}</p>
            <p className="mt-1 text-[11px] text-muted">The patient sees this in their portal.</p>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <div className="flex gap-2">
            <input
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Reply to the patient…"
              disabled={sending}
              className="flex-1 rounded-lg border border-border bg-canvas px-3 py-1.5 text-sm outline-none focus:border-primary"
            />
            <button
              onClick={send}
              disabled={sending || !reply.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-fg transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {sending ? "Sending…" : "Reply & resolve"} <Send className="h-3.5 w-3.5" />
            </button>
          </div>
          {err && <p className="mt-1 text-xs text-escalate">{err}</p>}
        </div>
      )}
    </div>
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
