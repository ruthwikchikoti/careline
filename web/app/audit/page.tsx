"use client";

import { Activity, FileClock, LockKeyhole, ServerOff } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";

const COLUMNS = ["Time", "Patient", "Call / event", "Verdict", "Confidence", "Risk", "Trace"];

export default function AuditPage() {
  useRequireAuth();

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
            </table>
          </div>
          <CardBody>
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-14 text-center">
              <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-canvas text-muted">
                <ServerOff className="h-6 w-6" />
              </span>
              <h2 className="text-sm font-semibold text-ink">Audit read API is not available</h2>
              <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
                Audit turns, calls, and events exist in the backend service, but there is no
                authenticated doctor-scoped HTTP read endpoint. The UI is ready for a{" "}
                <code className="rounded bg-canvas px-1.5 py-0.5">GET /audit</code> contract.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
