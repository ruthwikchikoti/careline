"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, UserPlus, Users } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useRequireAuth } from "@/lib/use-require-auth";

// Patients landing. There is no list-all endpoint (every read is tenant + id
// scoped, no-leak by design), so this opens a specific patient's record by id.
export default function PatientsPage() {
  useRequireAuth();
  const router = useRouter();
  const [patientId, setPatientId] = useState("");

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

        <form
          onSubmit={openRecord}
          className="space-y-3 rounded-2xl border border-border bg-surface p-5 shadow-soft"
        >
          <label htmlFor="patient-id" className="flex items-center gap-2 text-sm font-medium text-ink">
            <Users className="h-4 w-4 text-muted" /> Patient ID
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
          <p className="text-xs text-muted">
            New patient?{" "}
            <Link href="/patients/new" className="inline-flex items-center gap-1 text-primary hover:underline">
              <UserPlus className="h-3 w-3" /> Register a patient
            </Link>
          </p>
        </form>
      </div>
    </AppShell>
  );
}
