"use client";

import { useEffect, useState } from "react";
import { Activity, FileClock, LockKeyhole, ServerOff } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { VerdictPill } from "@/components/ui/VerdictPill";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";
import { getAuditLog, type AuditLog } from "@/lib/api";

const COLUMNS = ["Time", "Patient", "Call", "Verdict", "Confidence", "Risk", "Trace"];

export default function AuditPage() {
  useRequireAuth();

  const [log, setLog] = useState<AuditLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getAuditLog()
      .then((data) => active && setLog(data))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const turns = log?.turns ?? [];

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl space-y-6">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            <span className="text-xs font-semibold uppercase tracking-wide text-primary">
              Compliance trail
            </span>
          </div>
          <h1 className="text-2xl font-semibold text-ink">Audit log</h1>
          <p className="mt-1 text-sm text-muted">
            Per-call turns, terminal verdicts, safety scores, and retained trace skeletons.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardBody className="flex gap-3">
              <FileClock className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-semibold text-ink">Append-only clinical audit</p>
                <p className="mt-1 text-sm leading-6 text-muted">
                  Every call and turn keeps its verdict, confidence, risk, and agent-step trace.
                </p>
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardBody className="flex gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-semibold text-ink">DPDP-safe redaction</p>
                <p className="mt-1 text-sm leading-6 text-muted">
                  Erasure removes clinical text while retaining IDs, timestamps, verdicts, and
                  trace structure for compliance.
                </p>
              </div>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="Doctor activity" subtitle="Newest audit record first" />
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left">
              <thead className="border-b border-border bg-canvas">
                <tr>
                  {COLUMNS.map((column) => (
                    <th
                      key={column}
                      className="whitespace-nowrap px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted first:pl-6 last:pr-6"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              {turns.length > 0 && (
                <tbody>
                  {turns.map((turn) => (
                    <tr key={turn.turn_id} className="border-b border-border last:border-0">
                      <td className="whitespace-nowrap px-4 py-3 pl-6 text-sm text-muted">
                        {new Date(turn.logged_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-sm font-medium text-ink">{turn.patient_id}</td>
                      <td className="px-4 py-3 text-sm text-muted">{turn.call_id}</td>
                      <td className="px-4 py-3">
                        <VerdictPill verdict={turn.verdict} />
                      </td>
                      <td className="px-4 py-3 text-sm text-ink">
                        {(turn.confidence * 100).toFixed(0)}%
                      </td>
                      <td className="px-4 py-3 text-sm text-ink">{(turn.risk * 100).toFixed(0)}%</td>
                      <td className="px-4 py-3 pr-6 text-sm text-muted">
                        {turn.trace_steps.length} step{turn.trace_steps.length === 1 ? "" : "s"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
          {turns.length === 0 && (
            <CardBody>
              <EmptyState loading={loading} error={error} />
            </CardBody>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function EmptyState({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center px-6 py-14 text-sm text-muted">
        Loading audit trail…
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-14 text-center">
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-canvas text-muted">
        <ServerOff className="h-6 w-6" />
      </span>
      <h2 className="text-sm font-semibold text-ink">
        {error ? "Could not load the audit trail" : "No audit records yet"}
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
        {error ??
          "Audit turns are recorded as patients ask questions through the live spine. Run a call or the Live Console, then refresh to see verdicts, scores, and trace skeletons here."}
      </p>
    </div>
  );
}
