"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Loader2,
  Lock,
  MessageSquareText,
  Send,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  Trash2,
} from "lucide-react";
import {
  clearPatientHistory,
  getCarePlan,
  getPatientQuestions,
  patientAsk,
  type CarePlan,
  type FactRecord,
  type PatientQuestion,
} from "@/lib/api";
import { clearPatientToken, isPatientAuthenticated } from "@/lib/auth";
import { suggestionsFor } from "@/lib/suggestions";

const KIND_LABEL: Record<string, string> = {
  medication: "Medication",
  allergy: "Allergy",
  diagnosis: "Diagnosis",
  instruction: "Instruction",
  follow_up: "Follow-up",
  observation: "Observation",
};

// Order care-plan groups by clinical salience, not arrival order.
const KIND_ORDER = ["medication", "allergy", "diagnosis", "instruction", "follow_up", "observation"];

const SUGGESTIONS = [
  "What is my dose?",
  "When is my follow-up?",
  "Am I allergic to anything?",
];

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, (Date.now() - then) / 1000);
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 172800) return "yesterday";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function PatientPortalPage() {
  const router = useRouter();
  const [plan, setPlan] = useState<CarePlan | null>(null);
  const [questions, setQuestions] = useState<PatientQuestion[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [ready, setReady] = useState(false);
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  const loadQuestions = useCallback(
    () => getPatientQuestions().then(setQuestions).catch(() => {}),
    [],
  );

  useEffect(() => {
    if (!isPatientAuthenticated()) {
      router.replace("/patient/login");
      return;
    }
    Promise.all([getCarePlan().then(setPlan).catch(() => {}), loadQuestions()]).finally(() =>
      setReady(true),
    );
  }, [router, loadQuestions]);

  // Oldest → newest so the conversation reads top-to-bottom toward the composer.
  const thread = useMemo(
    () =>
      [...questions].sort(
        (a, b) => new Date(a.asked_at).getTime() - new Date(b.asked_at).getTime(),
      ),
    [questions],
  );

  // Starter questions derived from the patient's own care plan (generic fallback).
  const suggestions = useMemo(() => suggestionsFor(plan?.facts ?? [], SUGGESTIONS), [plan]);

  useEffect(() => {
    const reduce = typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    threadEndRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "end" });
  }, [thread.length, pending, asking]);

  async function ask(question?: string) {
    const q = (question ?? input).trim();
    if (!q || asking) return;
    setAsking(true);
    setPending(q); // optimistic: show the question immediately
    setInput("");
    try {
      await patientAsk(q);
      await loadQuestions(); // refresh thread (answers + escalations land here)
    } finally {
      setPending(null);
      setAsking(false);
    }
  }

  async function clearHistory() {
    if (clearing || (thread.length === 0 && !pending)) return;
    if (
      !window.confirm(
        "Clear your question history? This removes the conversation from your portal.",
      )
    )
      return;
    setClearing(true);
    try {
      await clearPatientHistory();
      setQuestions([]);
    } catch {
      // leave the thread as-is on failure
    } finally {
      setClearing(false);
    }
  }

  function signOut() {
    clearPatientToken();
    router.replace("/patient/login");
  }

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-muted">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading your portal…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      {/* header */}
      <header className="sticky top-0 z-20 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-fg shadow-soft">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <div className="leading-tight">
              <p className="text-sm font-semibold text-ink">Your care portal</p>
              <p className="text-xs text-muted">{plan?.patient_id ?? "—"}</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={clearHistory}
              disabled={clearing || (thread.length === 0 && !pending)}
              title="Clear your question history"
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-canvas hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-40"
            >
              {clearing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              <span className="hidden sm:inline">Clear</span>
            </button>
            <button
              onClick={signOut}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-canvas hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 gap-5 px-4 py-5 sm:px-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* care plan — collapsible on mobile, sits above the chat */}
        <div className="lg:hidden">
          <CarePlanDisclosure facts={plan?.facts ?? []} />
        </div>

        {/* conversation */}
        <section className="flex h-[calc(100dvh-9.5rem)] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-soft lg:h-[calc(100dvh-7.5rem)]">
          <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
            <MessageSquareText className="h-4 w-4 text-primary" />
            <h1 className="text-sm font-semibold text-ink">Ask a follow-up</h1>
            <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-answer-bg px-2 py-0.5 text-[11px] font-medium text-answer">
              <span className="h-1.5 w-1.5 rounded-full bg-answer" /> Secure
            </span>
          </div>

          {/* thread */}
          <div className="flex-1 space-y-5 overflow-y-auto px-4 py-5 sm:px-5">
            {thread.length === 0 && !pending ? (
              <EmptyState suggestions={suggestions} onPick={(q) => ask(q)} />
            ) : (
              <>
                {thread.map((q) => (
                  <Exchange key={q.turn_id} q={q} />
                ))}
                {pending && (
                  <div className="space-y-2.5">
                    <PatientBubble>{pending}</PatientBubble>
                    <AgentBubble tone="thinking">
                      <span className="flex items-center gap-2 text-muted">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking your care plan…
                      </span>
                    </AgentBubble>
                  </div>
                )}
              </>
            )}
            <div ref={threadEndRef} />
          </div>

          {/* composer */}
          <div className="border-t border-border bg-surface px-3 py-3 sm:px-4">
            {/* persistent suggestion chips — stay available after the first question */}
            {(thread.length > 0 || pending) && suggestions.length > 0 && (
              <div className="mb-2.5 flex gap-2 overflow-x-auto pb-1">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    disabled={asking}
                    className="shrink-0 rounded-full border border-border bg-canvas px-3 py-1 text-xs text-muted transition-colors hover:border-primary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
            <div className="flex items-end gap-2">
              <label htmlFor="ask" className="sr-only">
                Your question
              </label>
              <input
                id="ask"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && ask()}
                placeholder="Type your question…"
                disabled={asking}
                className="flex-1 rounded-xl border border-border bg-canvas px-3.5 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:opacity-50"
              />
              <button
                onClick={() => ask()}
                disabled={asking || !input.trim()}
                aria-label="Send question"
                className="inline-flex h-[42px] items-center gap-1.5 rounded-xl bg-primary px-4 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
              >
                {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                <span className="hidden sm:inline">{asking ? "Asking" : "Ask"}</span>
              </button>
            </div>
            <p className="mt-2 flex items-center gap-1.5 px-1 text-[11px] leading-4 text-muted">
              <Lock className="h-3 w-3" /> Private to you. Answered only from your doctor&apos;s
              approved plan — anything unsafe goes straight to your doctor.
            </p>
          </div>
        </section>

        {/* care plan sidebar — desktop */}
        <aside className="hidden lg:block">
          <div className="sticky top-24 rounded-2xl border border-border bg-surface p-5 shadow-soft">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ClipboardList className="h-4 w-4 text-primary" /> Your care plan
            </h2>
            <p className="mt-1 text-xs leading-5 text-muted">
              Your doctor&apos;s current, approved guidance.
            </p>
            <div className="mt-4">
              <CarePlanList facts={plan?.facts ?? []} />
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}

/* ----------------------------- conversation ----------------------------- */

function EmptyState({
  suggestions,
  onPick,
}: {
  suggestions: string[];
  onPick: (q: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 py-10 text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-muted text-primary">
        <Stethoscope className="h-7 w-7" />
      </span>
      <p className="text-sm font-semibold text-ink">How can we help today?</p>
      <p className="max-w-xs text-xs leading-5 text-muted">
        Ask anything about your care plan. Try one of these:
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="rounded-full border border-border bg-canvas px-3 py-1.5 text-xs text-ink transition-colors hover:border-primary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

/** One question + its resolution, rendered as a chat exchange. */
function Exchange({ q }: { q: PatientQuestion }) {
  return (
    <div className="space-y-2.5">
      <PatientBubble meta={relTime(q.asked_at)}>{q.question ?? "—"}</PatientBubble>

      {q.verdict === "answer" && q.answer_text && (
        <AgentBubble>{q.answer_text}</AgentBubble>
      )}

      {q.verdict === "clarify" && (
        <AgentBubble tone="clarify">
          {q.answer_text ?? "Could you share a bit more detail so I can answer safely?"}
        </AgentBubble>
      )}

      {q.escalated && q.doctor_reply && (
        <div className="ml-9 max-w-[88%]">
          <div className="rounded-2xl rounded-bl-md border border-answer/25 bg-answer-bg px-4 py-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-answer">
              <CheckCircle2 className="h-3.5 w-3.5" /> Your doctor replied
              {q.replied_at && <span className="font-normal text-muted">· {relTime(q.replied_at)}</span>}
            </p>
            <p className="mt-1 text-sm leading-6 text-ink">{q.doctor_reply}</p>
          </div>
        </div>
      )}

      {q.escalated && !q.doctor_reply && (
        <div className="ml-9 inline-flex max-w-[88%] items-center gap-2 rounded-2xl rounded-bl-md border border-escalate/20 bg-escalate-bg px-3 py-2 text-xs font-medium text-escalate">
          <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
          Sent to your doctor — we&apos;ll show their reply here.
        </div>
      )}
    </div>
  );
}

function PatientBubble({ children, meta }: { children: React.ReactNode; meta?: string }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[88%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-6 text-primary-fg">
        {children}
      </div>
      {meta && <span className="mt-1 px-1 text-[11px] text-muted">{meta}</span>}
    </div>
  );
}

function AgentBubble({
  children,
  tone = "answer",
}: {
  children: React.ReactNode;
  tone?: "answer" | "clarify" | "thinking";
}) {
  const bubble =
    tone === "clarify"
      ? "border-clarify/30 bg-clarify-bg text-ink"
      : "border-border bg-canvas text-ink";
  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-muted text-primary">
        {tone === "clarify" ? (
          <MessageSquareText className="h-4 w-4" />
        ) : (
          <Stethoscope className="h-4 w-4" />
        )}
      </span>
      <div
        className={`max-w-[88%] rounded-2xl rounded-bl-md border px-4 py-2.5 text-sm leading-6 ${bubble}`}
      >
        {children}
      </div>
    </div>
  );
}

/* ------------------------------ care plan ------------------------------- */

function groupFacts(facts: FactRecord[]): [string, FactRecord[]][] {
  const by: Record<string, FactRecord[]> = {};
  for (const f of facts) (by[f.kind] ??= []).push(f);
  return Object.entries(by).sort(
    ([a], [b]) =>
      (KIND_ORDER.indexOf(a) + 1 || 99) - (KIND_ORDER.indexOf(b) + 1 || 99),
  );
}

function CarePlanList({ facts }: { facts: FactRecord[] }) {
  if (facts.length === 0) {
    return <p className="text-sm text-muted">No approved guidance on file yet.</p>;
  }
  return (
    <div className="space-y-4">
      {groupFacts(facts).map(([kind, items]) => (
        <div key={kind}>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
            {KIND_LABEL[kind] ?? kind}
          </p>
          <ul className="space-y-1.5">
            {items.map((f) => (
              <li
                key={f.id}
                className="rounded-xl border border-border bg-canvas px-3 py-2 text-sm leading-6 text-ink"
              >
                {f.summary}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Mobile: care plan as a collapsed disclosure so the chat stays the focus. */
function CarePlanDisclosure({ facts }: { facts: FactRecord[] }) {
  return (
    <details className="group rounded-2xl border border-border bg-surface shadow-soft">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-ink [&::-webkit-details-marker]:hidden">
        <ClipboardList className="h-4 w-4 text-primary" />
        Your care plan
        <span className="rounded-full bg-primary-muted px-2 py-0.5 text-[11px] font-medium text-primary">
          {facts.length}
        </span>
        <ChevronDown className="ml-auto h-4 w-4 text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t border-border px-4 py-3">
        <CarePlanList facts={facts} />
      </div>
    </details>
  );
}
