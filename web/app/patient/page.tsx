"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CheckCircle2,
  ClipboardList,
  MessageSquareText,
  Send,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
} from "lucide-react";
import {
  getCarePlan,
  getPatientQuestions,
  patientAsk,
  type CarePlan,
  type PatientAnswer,
  type PatientQuestion,
} from "@/lib/api";
import { clearPatientToken, isPatientAuthenticated } from "@/lib/auth";

const KIND_LABEL: Record<string, string> = {
  medication: "Medication",
  instruction: "Instruction",
  diagnosis: "Diagnosis",
  observation: "Observation",
  allergy: "Allergy",
  follow_up: "Follow-up",
};

export default function PatientPortalPage() {
  const router = useRouter();
  const [plan, setPlan] = useState<CarePlan | null>(null);
  const [questions, setQuestions] = useState<PatientQuestion[]>([]);
  const [input, setInput] = useState("");
  const [answer, setAnswer] = useState<PatientAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [ready, setReady] = useState(false);

  const loadQuestions = useCallback(() => getPatientQuestions().then(setQuestions).catch(() => {}), []);

  useEffect(() => {
    if (!isPatientAuthenticated()) {
      router.replace("/patient/login");
      return;
    }
    Promise.all([getCarePlan().then(setPlan).catch(() => {}), loadQuestions()]).finally(() =>
      setReady(true),
    );
  }, [router, loadQuestions]);

  async function ask() {
    const q = input.trim();
    if (!q || asking) return;
    setAsking(true);
    setAnswer(null);
    try {
      const res = await patientAsk(q);
      setAnswer(res);
      setInput("");
      await loadQuestions(); // refresh history (escalations show up as "awaiting doctor")
    } catch {
      setAnswer({
        verdict: "escalate",
        answer_text: null,
        escalation_reason: "Something went wrong — please try again.",
        citations: [],
      });
    } finally {
      setAsking(false);
    }
  }

  function signOut() {
    clearPatientToken();
    router.replace("/patient/login");
  }

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-muted">
        Loading your portal…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink">Your care portal</p>
              <p className="text-xs text-muted">{plan?.patient_id}</p>
            </div>
          </div>
          <button onClick={signOut} className="text-xs text-muted hover:text-ink">
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        {/* ask */}
        <section className="rounded-2xl border border-border bg-surface p-5 shadow-soft">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <MessageSquareText className="h-4 w-4 text-primary" /> Ask a follow-up question
          </h2>
          <p className="mt-1 text-sm text-muted">
            CareLine answers only from your doctor&apos;s approved guidance, and escalates anything
            unsafe straight to your doctor.
          </p>
          <div className="mt-3 flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask()}
              placeholder="e.g. What is my Paracetamol dose?"
              disabled={asking}
              className="flex-1 rounded-lg border border-border bg-canvas px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <button
              onClick={ask}
              disabled={asking || !input.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {asking ? "Asking…" : "Ask"} <Send className="h-4 w-4" />
            </button>
          </div>

          {answer && (
            <div className="mt-3 rounded-xl border border-border bg-canvas p-4">
              {answer.verdict === "answer" ? (
                <div className="flex items-start gap-2">
                  <Stethoscope className="mt-0.5 h-4 w-4 shrink-0 text-answer" />
                  <p className="text-sm leading-6 text-ink">{answer.answer_text}</p>
                </div>
              ) : (
                <div className="flex items-start gap-2">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-escalate" />
                  <p className="text-sm leading-6 text-ink">
                    {answer.verdict === "escalate"
                      ? "I've passed this to your doctor to answer safely. You'll see their reply below once they respond."
                      : answer.escalation_reason ?? "Could you share a bit more detail?"}
                  </p>
                </div>
              )}
            </div>
          )}
        </section>

        {/* care plan */}
        <section className="rounded-2xl border border-border bg-surface p-5 shadow-soft">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <ClipboardList className="h-4 w-4 text-primary" /> Your care plan
          </h2>
          {plan && plan.facts.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {plan.facts.map((f) => (
                <li key={f.id} className="flex items-start gap-3 rounded-lg border border-border px-3 py-2">
                  <span className="mt-0.5 rounded-full bg-primary-muted px-2 py-0.5 text-[11px] font-medium text-primary">
                    {KIND_LABEL[f.kind] ?? f.kind}
                  </span>
                  <span className="text-sm leading-6 text-ink">{f.summary}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted">No approved guidance on file yet.</p>
          )}
        </section>

        {/* my questions */}
        <section className="rounded-2xl border border-border bg-surface p-5 shadow-soft">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <MessageSquareText className="h-4 w-4 text-primary" /> Your questions &amp; answers
          </h2>
          {questions.length > 0 ? (
            <ul className="mt-3 space-y-3">
              {questions.map((q) => (
                <li key={q.turn_id} className="rounded-xl border border-border px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-ink">{q.question ?? "—"}</p>
                    <span className="whitespace-nowrap text-xs text-muted">
                      {new Date(q.asked_at).toLocaleString()}
                    </span>
                  </div>

                  {q.verdict === "answer" && q.answer_text && (
                    <p className="mt-1 text-sm leading-6 text-muted">{q.answer_text}</p>
                  )}

                  {q.escalated && q.doctor_reply && (
                    <div className="mt-2 flex items-start gap-2 rounded-lg bg-answer-bg px-3 py-2">
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-answer" />
                      <div>
                        <p className="text-xs font-medium text-answer">Your doctor replied</p>
                        <p className="text-sm leading-6 text-ink">{q.doctor_reply}</p>
                      </div>
                    </div>
                  )}

                  {q.escalated && !q.doctor_reply && (
                    <p className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-escalate-bg px-2 py-0.5 text-xs font-medium text-escalate">
                      <ShieldAlert className="h-3 w-3" /> Sent to your doctor — awaiting reply
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted">You haven&apos;t asked anything yet.</p>
          )}
        </section>
      </main>
    </div>
  );
}
