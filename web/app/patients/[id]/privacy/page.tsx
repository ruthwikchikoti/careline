"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input, Label } from "@/components/ui/Input";
import { erasePatientData, type ErasureOut } from "@/lib/api";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function PrivacyPage() {
  useRequireAuth();
  const params = useParams();
  const patientId = String(params.id ?? "");

  const [confirmId, setConfirmId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ErasureOut | null>(null);

  const canDelete = confirmId.trim() === patientId && !result;

  async function handleErase(e: FormEvent) {
    e.preventDefault();
    if (!canDelete) return;
    setError(null);
    setLoading(true);
    try {
      const out = await erasePatientData(patientId);
      setResult(out);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erasure failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-6">
        <div>
          <Link href="/" className="text-sm text-primary hover:underline">
            ← Back to dashboard
          </Link>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Privacy &amp; data erasure</h1>
          <p className="mt-1 text-sm text-muted">DPDP right-to-erasure for patient {patientId}</p>
        </div>

        <Card className="border-escalate/30 bg-escalate-bg">
          <CardBody className="flex gap-3">
            <AlertTriangle className="h-5 w-5 shrink-0 text-escalate" />
            <div>
              <p className="text-sm font-medium text-ink">This action cannot be undone</p>
              <p className="mt-1 text-sm text-muted">
                Permanently erases all clinical data for this patient from Layer 1 (source of truth) and
                Layer 2 (memory/RAG). Audit entries are redacted.
              </p>
            </div>
          </CardBody>
        </Card>

        {result ? (
          <Card>
            <CardHeader title="Erasure complete" subtitle={`Patient ${result.patient_id}`} />
            <CardBody className="space-y-2 text-sm text-ink">
              <p>Layer 1 fields nulled: {result.layer1_nulled}</p>
              <p>Layer 2 namespace dropped: {result.layer2_dropped ? "yes" : "no"}</p>
              <p>Audit records redacted: {result.audit_redacted}</p>
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardHeader title="Confirm erasure" subtitle="Type the patient ID to enable delete" />
            <CardBody>
              <form onSubmit={handleErase} className="space-y-4">
                <div>
                  <Label htmlFor="confirm-id">Type {patientId} to confirm</Label>
                  <Input
                    id="confirm-id"
                    type="text"
                    value={confirmId}
                    onChange={(e) => setConfirmId(e.target.value)}
                    autoComplete="off"
                  />
                </div>

                {error ? <p className="text-sm text-escalate">{error}</p> : null}

                <Button
                  type="submit"
                  disabled={!canDelete || loading}
                  className="bg-escalate hover:bg-escalate/90"
                >
                  {loading ? "Erasing…" : "Delete all patient data"}
                </Button>
              </form>
            </CardBody>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
