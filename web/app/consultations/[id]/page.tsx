"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowRight, CheckCircle2, Circle, Loader2 } from "lucide-react";
import { ApproveButton } from "@/components/approval/ApproveButton";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import {
  extractFacts,
  stampConsent,
  type ApprovalOut,
  type ExtractOut,
} from "@/lib/api";
import { useConsultation } from "@/lib/api-hooks";
import { useRequireAuth } from "@/lib/use-require-auth";
import { cn } from "@/lib/cn";

function StepIndicator({ done, active }: { done: boolean; active: boolean }) {
  if (done) return <CheckCircle2 className="h-5 w-5 shrink-0 text-answer" />;
  if (active) return <Circle className="h-5 w-5 shrink-0 text-primary" />;
  return <Circle className="h-5 w-5 shrink-0 text-border" />;
}

export default function ConsultationDetailPage() {
  useRequireAuth();
  const params = useParams();
  const consultationId = String(params.id ?? "");

  const { data: consultation, isLoading, error, refetch } = useConsultation(consultationId);

  const [consentDone, setConsentDone] = useState(false);
  const [extractResult, setExtractResult] = useState<ExtractOut | null>(null);
  const [approvalResult, setApprovalResult] = useState<ApprovalOut | null>(null);
  const [consentLoading, setConsentLoading] = useState(false);
  const [extractLoading, setExtractLoading] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);
  const [purpose, setPurpose] = useState("post-consultation follow-up care");

  useEffect(() => {
    if (!consultation) return;
    if (consultation.fact_count > 0 || consultation.status === "approved") {
      setConsentDone(true);
    }
    if (consultation.fact_count > 0 && !extractResult) {
      setExtractResult({
        consultation_id: consultation.consultation_id,
        fact_count: consultation.fact_count,
        status: consultation.status,
      });
    }
    if (consultation.status === "approved" && !approvalResult) {
      setApprovalResult({
        consultation_id: consultation.consultation_id,
        status: consultation.status,
        applied_facts: consultation.fact_count,
        retired_facts: 0,
      });
    }
  }, [consultation, extractResult, approvalResult]);

  async function handleConsent() {
    setStepError(null);
    setConsentLoading(true);
    try {
      await stampConsent(consultationId, purpose.trim());
      setConsentDone(true);
      await refetch();
    } catch (err) {
      setStepError(err instanceof Error ? err.message : "Consent failed");
    } finally {
      setConsentLoading(false);
    }
  }

  async function handleExtract() {
    setStepError(null);
    setExtractLoading(true);
    try {
      const result = await extractFacts(consultationId);
      setExtractResult(result);
      await refetch();
    } catch (err) {
      setStepError(err instanceof Error ? err.message : "Extraction failed");
    } finally {
      setExtractLoading(false);
    }
  }

  function handleApproved(result: ApprovalOut) {
    setApprovalResult(result);
    void refetch();
  }

  const factCount = extractResult?.fact_count ?? consultation?.fact_count ?? 0;
  const isApproved = approvalResult?.status === "approved" || consultation?.status === "approved";
  const extractDone = Boolean(extractResult) || (consultation?.fact_count ?? 0) > 0;

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <Link href="/consultations" className="text-sm text-primary hover:underline">
            ← Back to consultations
          </Link>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Consultation approval</h1>
          <p className="mt-1 text-sm text-muted">
            Human-in-the-loop: consent → extract drafted facts → one-tap approve into the live record.
          </p>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading consultation…
          </div>
        ) : null}

        {error ? (
          <p className="text-sm text-escalate">
            {error instanceof Error ? error.message : "Failed to load consultation"}
          </p>
        ) : null}

        {consultation ? (
          <Card>
            <CardHeader
              title={consultation.consultation_id}
              subtitle={`Patient ${consultation.patient_id} · status ${consultation.status}`}
            />
            <CardBody className="space-y-6">
              {/* Step 1: Consent */}
              <div className="flex gap-4">
                <StepIndicator done={consentDone} active={!consentDone && !isApproved} />
                <div className="min-w-0 flex-1 space-y-3">
                  <div>
                    <h3 className="text-sm font-medium text-ink">1. Patient consent</h3>
                    <p className="text-sm text-muted">
                      Stamp explicit consent before any extraction or approval (DPDP fail-closed).
                    </p>
                  </div>
                  {consentDone ? (
                    <p className="text-sm text-answer">Consent stamped — processing authorised.</p>
                  ) : (
                    <div className="space-y-3">
                      <Input
                        type="text"
                        value={purpose}
                        onChange={(e) => setPurpose(e.target.value)}
                      />
                      <Button onClick={handleConsent} disabled={consentLoading || isApproved}>
                        {consentLoading ? "Stamping…" : "Stamp consent"}
                      </Button>
                    </div>
                  )}
                </div>
              </div>

              {/* Step 2: Extract */}
              <div className={cn("flex gap-4", !consentDone && "opacity-50")}>
                <StepIndicator done={extractDone} active={consentDone && !extractDone && !isApproved} />
                <div className="min-w-0 flex-1 space-y-3">
                  <div>
                    <h3 className="text-sm font-medium text-ink">2. Extract facts</h3>
                    <p className="text-sm text-muted">
                      Run extraction to draft facts from the transcript. Drafts are not yet live context.
                    </p>
                  </div>
                  {extractDone ? (
                    <p className="text-sm text-answer">
                      {factCount} fact{factCount === 1 ? "" : "s"} drafted — ready for approval.
                    </p>
                  ) : (
                    <Button onClick={handleExtract} disabled={!consentDone || extractLoading || isApproved}>
                      {extractLoading ? "Extracting…" : "Extract facts"}
                    </Button>
                  )}
                </div>
              </div>

              {/* Step 3: Approve */}
              <div className={cn("flex gap-4", !extractDone && "opacity-50")}>
                <StepIndicator done={isApproved} active={extractDone && !isApproved} />
                <div className="min-w-0 flex-1 space-y-3">
                  <div>
                    <h3 className="text-sm font-medium text-ink">3. One-tap approve</h3>
                    <p className="text-sm text-muted">
                      Promote drafted facts into the patient&apos;s currently-valid record (with supersession).
                    </p>
                  </div>
                  {isApproved && approvalResult ? (
                    <p className="text-sm text-answer">
                      {approvalResult.applied_facts} fact{approvalResult.applied_facts === 1 ? "" : "s"} applied ·{" "}
                      {approvalResult.retired_facts} retired
                    </p>
                  ) : (
                    <ApproveButton
                      consultationId={consultationId}
                      factCount={factCount}
                      disabled={!extractDone || isApproved}
                      onApproved={handleApproved}
                    />
                  )}
                </div>
              </div>

              {stepError ? <p className="text-sm text-escalate">{stepError}</p> : null}
            </CardBody>
          </Card>
        ) : null}

        {consultation && isApproved && approvalResult ? (
          <Card className="border-answer/30 bg-answer-bg">
            <CardBody className="space-y-5">
              <div className="flex gap-3">
                <CheckCircle2 className="h-6 w-6 shrink-0 text-answer" />
                <div>
                  <h2 className="text-lg font-semibold text-ink">Consultation approved</h2>
                  <p className="mt-1 text-sm text-muted">
                    Facts are now live context for patient{" "}
                    <span className="font-medium text-ink">{consultation.patient_id}</span>.
                  </p>
                  <p className="mt-2 text-sm text-answer">
                    {approvalResult.applied_facts} fact{approvalResult.applied_facts === 1 ? "" : "s"}{" "}
                    applied · {approvalResult.retired_facts} retired
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <Link
                  href={`/patients/${encodeURIComponent(consultation.patient_id)}`}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90"
                >
                  View patient record
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <Link
                  href="/consultations"
                  className="inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-canvas"
                >
                  New consultation
                </Link>
              </div>
            </CardBody>
          </Card>
        ) : null}
      </div>
    </AppShell>
  );
}
