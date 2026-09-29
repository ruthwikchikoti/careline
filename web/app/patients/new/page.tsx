"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input, Label } from "@/components/ui/Input";
import { registerPatient } from "@/lib/api";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function RegisterPatientPage() {
  useRequireAuth();
  const router = useRouter();
  const [patientId, setPatientId] = useState("");
  const [callerId, setCallerId] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      const result = await registerPatient({
        patient_id: patientId.trim(),
        caller_id: callerId.trim(),
        pin,
      });
      setSuccess(`Registered ${result.patient_id} under ${result.doctor_id}`);
      setTimeout(() => router.push("/"), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Register patient</h1>
          <p className="mt-1 text-sm text-muted">
            Add a patient identity for caller lookup. PIN is hashed server-side and never stored in plain text.
          </p>
        </div>

        <Card>
          <CardHeader title="Patient identity" subtitle="One patient per call — tenant-scoped registration" />
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
                <Label htmlFor="caller-id">Caller ID</Label>
                <Input
                  id="caller-id"
                  type="text"
                  required
                  value={callerId}
                  onChange={(e) => setCallerId(e.target.value)}
                  placeholder="+91-9876543210"
                />
              </div>

              <div>
                <Label htmlFor="pin">PIN (4–12 characters)</Label>
                <Input
                  id="pin"
                  type="password"
                  required
                  minLength={4}
                  maxLength={12}
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                />
              </div>

              {error ? <p className="text-sm text-escalate">{error}</p> : null}
              {success ? <p className="text-sm text-answer">{success}</p> : null}

              <Button type="submit" disabled={loading}>
                {loading ? "Registering…" : "Register patient"}
              </Button>
            </form>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
