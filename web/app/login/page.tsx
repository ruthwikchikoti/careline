"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, ShieldCheck } from "lucide-react";
import { login } from "@/lib/api";
import { isAuthenticated, setToken } from "@/lib/auth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Label } from "@/components/ui/Input";

export default function LoginPage() {
  const router = useRouter();
  const [doctorId, setDoctorId] = useState("dr-asha");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) router.replace("/");
  }, [router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await login(doctorId.trim());
      setToken(access_token);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* Brand panel — visible on larger screens */}
      <aside className="relative hidden w-[42%] flex-col justify-between overflow-hidden bg-primary p-10 text-primary-fg lg:flex xl:p-14">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-16 -left-16 h-56 w-56 rounded-full bg-white/10"
        />

        <div className="relative">
          <div className="mb-8 flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/15 backdrop-blur-sm">
              <ShieldCheck className="h-6 w-6" />
            </span>
            <span className="text-2xl font-semibold tracking-tight">CareLine</span>
          </div>
          <h1 className="max-w-sm text-3xl font-semibold leading-tight tracking-tight xl:text-4xl">
            Doctor workspace for post-consultation follow-up care
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-primary-fg/85">
            Manage approved patient context, run consultations, and review human-in-the-loop
            approvals before facts go live.
          </p>
        </div>

        <p className="relative text-xs text-primary-fg/70">
          Uncertainty always escalates to the doctor. One patient per call.
        </p>
      </aside>

      {/* Sign-in panel */}
      <main className="flex flex-1 flex-col items-center justify-center px-4 py-10 sm:px-6">
        <div className="mb-8 flex items-center gap-2 lg:hidden">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-fg">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <span className="text-xl font-semibold text-ink">CareLine</span>
        </div>

        <Card className="w-full max-w-md shadow-md">
          <div className="p-8">
            <div className="mb-8">
              <h2 className="text-2xl font-semibold text-ink">Sign in</h2>
              <p className="mt-1.5 text-sm text-muted">
                Enter your doctor ID to access the clinical workspace.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <Label htmlFor="doctor-id">Doctor ID</Label>
                <Input
                  id="doctor-id"
                  type="text"
                  required
                  autoComplete="username"
                  value={doctorId}
                  onChange={(e) => setDoctorId(e.target.value)}
                  placeholder="dr-asha"
                />
                <p className="mt-1.5 text-xs text-muted">Demo: use <code className="rounded bg-canvas px-1">dr-asha</code></p>
              </div>

              {error ? (
                <div className="rounded-lg border border-escalate/20 bg-escalate-bg px-3 py-2 text-sm text-escalate">
                  {error}
                </div>
              ) : null}

              <Button type="submit" className="h-11 w-full text-base" disabled={loading}>
                {loading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Signing in…
                  </>
                ) : (
                  "Sign in"
                )}
              </Button>
            </form>
          </div>
        </Card>
      </main>
    </div>
  );
}
