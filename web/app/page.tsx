"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Pill, ScrollText, ShieldCheck } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { getDemoPatient, type DemoPatient } from "@/lib/api";

export default function DashboardPage() {
  const [patient, setPatient] = useState<DemoPatient | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDemoPatient().then(setPatient).catch((e) => setError(String(e)));
  }, []);

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-muted">
            One patient, one call. The agent answers only from approved, currently-valid
            context — and hands the call to you the moment anything is unsafe.
          </p>
        </div>

        <Card>
          <CardHeader
            title="Demo patient"
            subtitle={
              patient
                ? `${patient.patient_id} · under ${patient.doctor_id} · reasoning: ${patient.backend}`
                : "Loading…"
            }
            action={
              <Link href="/console">
                <Button>
                  Start a call <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            }
          />
          <CardBody>
            {error ? (
              <p className="text-sm text-escalate">
                Couldn&apos;t reach the demo API. Start it with{" "}
                <code className="rounded bg-canvas px-1">uvicorn careline.demo_server:app</code>.
              </p>
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {patient?.current_facts.map((f) => (
                  <li key={f.id} className="flex items-start gap-3 rounded-xl border border-border p-3">
                    <span className="mt-0.5 text-primary">
                      {f.kind === "medication" ? <Pill className="h-4 w-4" /> : <ScrollText className="h-4 w-4" />}
                    </span>
                    <div>
                      <p className="text-xs font-medium uppercase tracking-wide text-muted">{f.kind}</p>
                      <p className="text-sm text-ink">{f.summary}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardBody className="flex items-center gap-3">
            <ShieldCheck className="h-5 w-5 shrink-0 text-primary" />
            <p className="text-sm text-muted">
              Superseded facts (a discontinued antibiotic, an expired diet) are deliberately
              absent above — they never reach the answer path.
            </p>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
