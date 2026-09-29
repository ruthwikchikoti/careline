"use client";

import Link from "next/link";
import { ArrowRight, Loader2, ShieldCheck } from "lucide-react";
import { ConsultationRow } from "@/components/approval/ConsultationRow";
import { AppShell } from "@/components/shell/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useConsultations } from "@/lib/api-hooks";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function DashboardPage() {
  useRequireAuth();
  const { data: consultations, isLoading, error } = useConsultations();
  const recent = consultations?.slice(0, 3) ?? [];

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
            title="Recent activity"
            subtitle="Consultations under your doctor account"
            action={
              <Link
                href="/consultations"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90"
              >
                New consultation <ArrowRight className="h-4 w-4" />
              </Link>
            }
          />
          <CardBody>
            {isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading recent consultations…
              </div>
            ) : error ? (
              <p className="text-sm text-escalate">
                {error instanceof Error ? error.message : "Failed to load consultations"}
              </p>
            ) : recent.length > 0 ? (
              <div className="space-y-4">
                <ul className="space-y-2">
                  {recent.map((c) => (
                    <li key={c.consultation_id}>
                      <ConsultationRow consultation={c} />
                    </li>
                  ))}
                </ul>
                {(consultations?.length ?? 0) > 3 ? (
                  <Link href="/consultations" className="text-sm text-primary hover:underline">
                    View all consultations →
                  </Link>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted">
                No consultations yet.{" "}
                <Link href="/consultations" className="text-primary hover:underline">
                  Create your first consultation
                </Link>
                .
              </p>
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
