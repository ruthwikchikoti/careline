"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ClipboardList, Loader2, ShieldOff } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { FactCard } from "@/components/record/FactCard";
import { Timeline } from "@/components/timeline/Timeline";
import { usePatientRecord } from "@/lib/api-hooks";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function PatientRecordPage() {
  useRequireAuth();
  const params = useParams();
  const patientId = String(params.id ?? "");

  const { data: record, isLoading, error } = usePatientRecord(patientId);

  const current = record?.current ?? [];
  const history = record?.history ?? [];

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <Link href="/consultations" className="text-sm text-primary hover:underline">
            ← Back to consultations
          </Link>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Patient record</h1>
          <p className="mt-1 text-sm text-muted">
            Patient <span className="font-medium text-ink">{patientId}</span> · the doctor&apos;s
            approved, currently-valid context — plus the superseded history.
          </p>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading record…
          </div>
        ) : null}

        {error ? (
          <div className="flex items-start gap-2 rounded-2xl border border-border bg-surface p-4 text-sm text-escalate shadow-soft">
            <ShieldOff className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {error instanceof Error ? error.message : "Failed to load patient record"}
            </span>
          </div>
        ) : null}

        {record ? (
          <>
            {/* Current valid slice */}
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
                  Current context
                </h2>
                <span className="text-xs text-muted">
                  {current.length} valid fact{current.length === 1 ? "" : "s"}
                </span>
              </div>

              {current.length === 0 ? (
                <div className="flex items-center gap-3 rounded-2xl border border-dashed border-border p-8 text-sm text-muted">
                  <ClipboardList className="h-5 w-5 shrink-0" />
                  No approved facts yet. Run a consultation → extract → approve to build this
                  patient&apos;s record.
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  {current.map((fact) => (
                    <FactCard key={fact.id} fact={fact} />
                  ))}
                </div>
              )}
            </section>

            {/* Temporal lane: current + superseded */}
            <section className="space-y-3">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
                History timeline
              </h2>
              <div className="rounded-2xl border border-border bg-surface p-5 shadow-soft">
                <Timeline items={[...current, ...history]} />
              </div>
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
