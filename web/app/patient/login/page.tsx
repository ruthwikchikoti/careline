"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Phone, ShieldCheck } from "lucide-react";
import { patientLogin } from "@/lib/api";
import { isPatientAuthenticated } from "@/lib/auth";

export default function PatientLoginPage() {
  const router = useRouter();
  const [patientId, setPatientId] = useState("ravi-kumar");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isPatientAuthenticated()) router.replace("/patient");
  }, [router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await patientLogin(patientId.trim(), pin.trim());
      router.push("/patient");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-fg">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold text-ink">CareLine</span>
          <span className="ml-auto rounded-full bg-primary-muted px-2.5 py-0.5 text-xs font-medium text-primary">
            Patient
          </span>
        </div>

        <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
          <h1 className="text-xl font-semibold text-ink">Sign in to your care portal</h1>
          <p className="mt-1 text-sm text-muted">
            Use your patient ID and PIN — the same details you use on the phone line.
          </p>

          <form onSubmit={handleSubmit} className="mt-5 space-y-3">
            <div>
              <label htmlFor="pid" className="mb-1 block text-sm font-medium text-ink">
                Patient ID
              </label>
              <input
                id="pid"
                value={patientId}
                onChange={(e) => setPatientId(e.target.value)}
                placeholder="e.g. ravi-kumar"
                className="w-full rounded-lg border border-border bg-canvas px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label htmlFor="pin" className="mb-1 block text-sm font-medium text-ink">
                PIN
              </label>
              <input
                id="pin"
                type="password"
                inputMode="numeric"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                placeholder="••••"
                className="w-full rounded-lg border border-border bg-canvas px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>

            {error && <p className="text-sm text-escalate">{error}</p>}

            <button
              type="submit"
              disabled={loading || !patientId.trim() || !pin.trim()}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {loading ? "Signing in…" : "Sign in"} <ArrowRight className="h-4 w-4" />
            </button>
          </form>

          <p className="mt-4 flex items-center gap-1.5 text-xs text-muted">
            <Phone className="h-3.5 w-3.5" /> Demo: patient ID <span className="font-medium text-ink">ravi-kumar</span>, PIN <span className="font-medium text-ink">1234</span>
          </p>
        </div>

        <p className="mt-4 text-center text-xs text-muted">
          Are you a doctor?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Doctor sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
