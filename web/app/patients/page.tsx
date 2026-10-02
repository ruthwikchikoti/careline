"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, FileText, UserPlus, Users } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useRequireAuth } from "@/lib/use-require-auth";
import { listPatients, type PatientOut } from "@/lib/api";

// Patients landing. Reads are still tenant + id scoped (no-leak by design); the
// list endpoint only ever returns *this* doctor's own patients, so showing them
// here is safe and saves typing an id. The open-by-id box stays as a fallback.
export default function PatientsPage() {
  useRequireAuth();
  const router = useRouter();
  const [patientId, setPatientId] = useState("");
  const [patients, setPatients] = useState<PatientOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listPatients()
      .then(setPatients)
      .catch((e) => {
        setPatients([]);
        setError(String(e));
      });
  }, []);

  function openRecord(e: React.FormEvent) {
    e.preventDefault();
    const id = patientId.trim();
    if (id) router.push(`/patients/${encodeURIComponent(id)}`);
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Patients</h1>
          <p className="mt-1 text-sm text-muted">
            Open a patient&apos;s record to see their approved, currently-valid context and the
            superseded history.
          </p>
        </div>

        {/* the doctor's own patients */}
        <section className="rounded-2xl border border-border bg-surface shadow-soft">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Users className="h-4 w-4 text-muted" /> Your patients
              {patients && patients.length > 0 && (
                <span className="rounded-full bg-primary-muted px-2 py-0.5 text-xs font-medium text-primary">
                  {patients.length}
                </span>
              )}
            </h2>
            <Link
              href="/patients/new"
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              <UserPlus className="h-3 w-3" /> Register
            </Link>
          </div>

          {patients === null ? (
            <p className="px-5 py-6 text-sm text-muted">Loading your patients…</p>
          ) : patients.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted">
              No patients registered yet.{" "}
              <Link href="/patients/new" className="text-primary hover:underline">
                Register your first patient
              </Link>
              .
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {patients.map((p) => (
                <li key={p.patient_id}>
                  <Link
                    href={`/patients/${encodeURIComponent(p.patient_id)}`}
                    className="flex items-center justify-between px-5 py-3 hover:bg-canvas"
                  >
                    <span className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-muted text-primary">
                        <Users className="h-4 w-4" />
                      </span>
                      <span className="text-sm font-medium text-ink">{p.patient_id}</span>
                    </span>
                    <span className="flex items-center gap-4 text-xs text-muted">
                      <span className="inline-flex items-center gap-1">
                        <FileText className="h-3 w-3" />
                        {p.fact_count} approved fact{p.fact_count === 1 ? "" : "s"}
                      </span>
                      <ArrowRight className="h-4 w-4" />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* open by id — fallback for a patient not in the list yet */}
        <form
          onSubmit={openRecord}
          className="space-y-3 rounded-2xl border border-border bg-surface p-5 shadow-soft"
        >
          <label htmlFor="patient-id" className="flex items-center gap-2 text-sm font-medium text-ink">
            <Users className="h-4 w-4 text-muted" /> Open by ID
          </label>
          <div className="flex gap-2">
            <Input
              id="patient-id"
              type="text"
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
              placeholder="e.g. patient-A"
            />
            <Button type="submit" disabled={!patientId.trim()}>
              Open record <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
          {error && <p className="text-xs text-muted">Could not load the list — open by ID still works.</p>}
        </form>
      </div>
    </AppShell>
  );
}
