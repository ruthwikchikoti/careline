"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ClipboardList, Loader2 } from "lucide-react";
import { ConsultationRow } from "@/components/approval/ConsultationRow";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { createConsultation } from "@/lib/api";
import { useConsultations } from "@/lib/api-hooks";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function ConsultationsPage() {
  useRequireAuth();
  const router = useRouter();
  const { data: consultations, isLoading, error } = useConsultations();
  const [patientId, setPatientId] = useState("");
  const [transcript, setTranscript] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setLoading(true);
    try {
      const consultation = await createConsultation({
        patient_id: patientId.trim(),
        transcript: transcript.trim() || null,
      });
      router.push(`/consultations/${consultation.consultation_id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create consultation");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Consultations</h1>
          <p className="mt-1 text-sm text-muted">
            Track A: transcript → extraction → one-tap doctor approval before facts go live.
          </p>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="New consultation"
              subtitle="Open a draft for a patient — consent and extraction happen on the detail screen"
            />
            <CardBody>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <Label htmlFor="patient-id">Patient ID</Label>
                  <Input
                    id="patient-id"
                    type="text"
                    required
                    value={patientId}
                    onChange={(e) => setPatientId(e.target.value)}
                  />
                </div>

                <div>
                  <Label htmlFor="transcript">Transcript (optional)</Label>
                  <Textarea
                    id="transcript"
                    rows={6}
                    value={transcript}
                    onChange={(e) => setTranscript(e.target.value)}
                    placeholder="Doctor: Take amoxicillin 500mg twice daily for 7 days…"
                  />
                </div>

                {formError ? <p className="text-sm text-escalate">{formError}</p> : null}

                <Button type="submit" disabled={loading}>
                  {loading ? "Creating…" : "Create consultation"}
                </Button>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Recent consultations" subtitle="Newest first under your doctor account" />
            <CardBody>
              {isLoading ? (
                <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading consultations…
                </div>
              ) : error ? (
                <p className="text-sm text-escalate">
                  {error instanceof Error ? error.message : "Failed to load consultations"}
                </p>
              ) : consultations && consultations.length > 0 ? (
                <ul className="space-y-2">
                  {consultations.map((c) => (
                    <li key={c.consultation_id}>
                      <ConsultationRow consultation={c} />
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <ClipboardList className="mb-3 h-10 w-10 text-muted" />
                  <p className="text-sm font-medium text-ink">No consultations yet</p>
                  <p className="mt-1 max-w-xs text-sm text-muted">
                    Create a consultation on the left. You&apos;ll be taken to the HITL approval flow.
                  </p>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
