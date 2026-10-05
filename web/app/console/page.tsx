"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, Phone, Send, Stethoscope, Trash2, User } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { VerdictPill } from "@/components/ui/VerdictPill";
import { ConsoleAnswerPanel } from "./_answer/ConsoleAnswerPanel";
import {
  ask,
  getDemoPatient,
  getPatientRecord,
  listPatients,
  type AnswerResult,
  type PatientOut,
} from "@/lib/api";
import { suggestionsFor } from "@/lib/suggestions";
import { cn } from "@/lib/cn";

interface Turn {
  question: string;
  result: AnswerResult | null; // null while the agent is still working
}

function ThinkingBubble() {
  return (
    <div className="inline-flex items-center gap-3 rounded-2xl rounded-tl-sm border border-border bg-surface px-4 py-3 shadow-soft">
      <span className="flex gap-1" aria-hidden>
        <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-primary" />
      </span>
      <span className="text-sm text-muted">
        Checking the patient&apos;s approved record…
      </span>
    </div>
  );
}

// Generic fallbacks — used for the demo patient or when a record can't be loaded.
const EXAMPLES = [
  "soft diet post surgery",
  "should I take amoxicillin",
  "Can I eat sweets post-surgery given my diabetes?",
  "I have chest pain",
];

// The agent route, in order — animated live while a question is in flight so the
// console shows *which step it's on* in real time, then settles into the real trace.
const PIPELINE = [
  { key: "triage", label: "Triage", agent: "Triage" },
  { key: "retrieve", label: "Retrieval", agent: "Retriever" },
  { key: "reason", label: "Reason", agent: "Reasoner" },
  { key: "verify", label: "Verify", agent: "Verifier" },
  { key: "gate", label: "Gate", agent: "Gatekeeper" },
];

function LivePipeline() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const t = setInterval(
      () => setStep((s) => Math.min(s + 1, PIPELINE.length - 1)),
      550,
    );
    return () => clearInterval(t);
  }, []);
  return (
    <ol className="relative space-y-0" aria-label="Agent pipeline running">
      {PIPELINE.map((p, i) => {
        const done = i < step;
        const running = i === step;
        const isLast = i === PIPELINE.length - 1;
        return (
          <li key={p.key} className="relative flex gap-3 pb-4">
            {!isLast && (
              <span className="absolute left-[7px] top-4 h-full w-px bg-border" aria-hidden />
            )}
            <span
              className={cn(
                "relative z-10 mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2 transition-colors",
                done && "border-answer bg-answer",
                running && "animate-pulse border-primary bg-primary",
                !done && !running && "border-border bg-transparent",
              )}
            />
            <div className={cn("min-w-0 flex-1", !done && !running && "opacity-40")}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-ink">{p.label}</span>
                <span className="rounded-full bg-canvas px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                  {p.agent}
                </span>
                {running && (
                  <span className="animate-pulse text-[10px] font-semibold uppercase tracking-wide text-primary">
                    running…
                  </span>
                )}
                {done && (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-answer">
                    done
                  </span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// Map each backend rail/gate to the agent that owns it (for the per-step header).
const AGENT_OF: Record<string, string> = {
  retrieval: "Retriever",
  red_flag_rail: "Triage",
  multi_condition_tripwire: "Triage",
  conversational_rail: "Triage",
  reasoner: "Reasoner",
  verifier: "Verifier",
  scope_gate: "Gatekeeper",
  risk_gate: "Gatekeeper",
  cross_condition_gate: "Gatekeeper",
  confidence_staleness_gate: "Gatekeeper",
  independent_verification_gate: "Verifier",
  final_verdict: "Decision",
};
const humanizeStep = (n: string) =>
  n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// A LangSmith-style trace: each step is a clickable span; expand it to see THAT
// step's JSON. The deciding (terminal) and final steps also carry the turn's
// outputs (verdict / confidence / risk / citations / answer) as their "output".
function ExpandableTrace({ result }: { result: AnswerResult }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="relative space-y-0">
      {result.trace.map((step, i) => {
        const isOpen = open === i;
        const isLast = i === result.trace.length - 1;
        const skipped = step.status === "skipped";
        const dot =
          step.status === "pass"
            ? "border-answer bg-answer"
            : skipped
              ? "border-border bg-transparent"
              : result.verdict === "clarify"
                ? "border-clarify bg-clarify"
                : "border-escalate bg-escalate";
        const payload =
          isLast || step.status === "terminal"
            ? {
                ...step,
                verdict: result.verdict,
                confidence: result.confidence,
                risk: result.risk,
                citations: result.citations,
                answer_text: result.answer_text,
                escalation_reason: result.escalation_reason,
              }
            : step;
        return (
          <li key={i} className="relative flex gap-3 pb-2">
            {!isLast && (
              <span className="absolute left-[7px] top-5 h-full w-px bg-border" aria-hidden />
            )}
            <span
              className={cn(
                "relative z-10 mt-1.5 h-3.5 w-3.5 shrink-0 rounded-full border-2",
                dot,
                skipped && "opacity-50",
              )}
            />
            <div className="min-w-0 flex-1">
              <button
                onClick={() => setOpen(isOpen ? null : i)}
                className="flex w-full items-center gap-2 text-left"
              >
                <span className={cn("text-sm font-medium text-ink", skipped && "opacity-45")}>
                  {humanizeStep(step.name)}
                </span>
                <span className="rounded-full bg-canvas px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                  {AGENT_OF[step.name] ?? "Agent"}
                </span>
                {step.status === "terminal" && (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-escalate">
                    terminal
                  </span>
                )}
                <ChevronDown
                  className={cn(
                    "ml-auto h-3.5 w-3.5 shrink-0 text-muted transition-transform",
                    isOpen && "rotate-180",
                  )}
                />
              </button>
              {isOpen && (
                <pre className="mt-1.5 max-h-56 overflow-auto rounded-lg border border-border bg-canvas px-2.5 py-2 text-[10px] leading-relaxed text-ink">
                  {JSON.stringify(payload, null, 2)}
                </pre>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default function ConsolePage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Optional: ask against a real registered patient (Mongo-persisted facts) when
  // signed in. Blank → the bundled demo patient.
  const [patientId, setPatientId] = useState("");
  // The signed-in doctor's registered patients, for the picker. Empty when
  // anonymous (no token) — the console still works against the demo patient.
  const [patients, setPatients] = useState<PatientOut[]>([]);
  // Suggested follow-ups — derived from the selected patient's real record, or
  // the generic examples for the demo patient.
  const [suggestions, setSuggestions] = useState<string[]>(EXAMPLES);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listPatients()
      .then(setPatients)
      .catch(() => setPatients([])); // anonymous / no token → demo only
  }, []);

  useEffect(() => {
    // Switching the patient starts a fresh call — clear the previous patient's
    // conversation and trace so they don't bleed across records.
    setTurns([]);
    setInput("");
    setError(null);
    const id = patientId.trim();
    let active = true;
    // Derive the starter questions from whoever is selected: a registered patient's
    // record, or the bundled demo patient when none is picked.
    const facts = id
      ? getPatientRecord(id).then((rec) => rec.current)
      : getDemoPatient().then((demo) => demo.current_facts);
    facts
      .then((fs) => active && setSuggestions(suggestionsFor(fs, EXAMPLES)))
      .catch(() => active && setSuggestions(EXAMPLES));
    return () => {
      active = false;
    };
  }, [patientId]);

  const clearChat = () => {
    setTurns([]);
    setInput("");
    setError(null);
  };

  const selected = patients.find((p) => p.patient_id === patientId);
  const callLabel = selected
    ? `${selected.patient_id} · ${selected.fact_count} approved fact${
        selected.fact_count === 1 ? "" : "s"
      }`
    : patientId.trim()
      ? patientId.trim()
      : "Ravi K. (demo patient)";

  // The trace panel follows the most recent *completed* turn.
  const active = [...turns].reverse().find((t) => t.result)?.result ?? null;

  const scrollToEnd = () =>
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 0);

  async function submit(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    setLoading(true);
    setError(null);
    // Show the patient's message immediately + a pending agent turn.
    setTurns((t) => [...t, { question: q, result: null }]);
    scrollToEnd();
    try {
      const result = await ask(q, patientId.trim() || undefined);
      // Fill in the result on the pending (last) turn.
      setTurns((t) =>
        t.map((turn, i) => (i === t.length - 1 ? { ...turn, result } : turn)),
      );
    } catch (e) {
      setError(String(e));
      // Drop the pending turn so the chat doesn't hang on a spinner.
      setTurns((t) => t.slice(0, -1));
    } finally {
      setLoading(false);
      scrollToEnd();
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      {/* header */}
      <header className="flex items-center justify-between border-b border-border bg-surface px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-muted hover:text-ink">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-muted text-primary">
            <Phone className="h-4 w-4" />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">
              Live call · {callLabel}
            </p>
            <p className="text-xs text-muted">Answers only from approved, currently-valid context</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {patients.length > 0 ? (
            <select
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
              title="Choose which patient's approved record to answer against"
              className="hidden w-60 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs outline-none focus:border-primary sm:block"
            >
              <option value="">Ravi K. (demo patient)</option>
              {patients.map((p) => (
                <option key={p.patient_id} value={p.patient_id}>
                  {p.patient_id} · {p.fact_count} approved
                </option>
              ))}
            </select>
          ) : (
            <input
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
              placeholder="patient ID (blank = demo)"
              title="Sign in to pick a registered patient from the list"
              className="hidden w-52 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs outline-none focus:border-primary sm:block"
            />
          )}
          {turns.length > 0 && (
            <button
              onClick={clearChat}
              title="Clear the conversation"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:border-primary hover:text-primary"
            >
              <Trash2 className="h-3.5 w-3.5" /> Clear
            </button>
          )}
          <span className="hidden items-center gap-1.5 text-xs font-medium text-answer sm:flex">
            <span className="h-2 w-2 rounded-full bg-answer" /> on the line
          </span>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-6xl flex-1 grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_360px]">
        {/* conversation */}
        <section className="flex flex-col">
          <div className="flex-1 space-y-4">
            {turns.length === 0 && (
              <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted">
                {selected
                  ? `Ask a follow-up as ${selected.patient_id}. Try one from their record:`
                  : "Ask a follow-up question as the patient. Try one:"}
                <div className="mt-3 flex flex-wrap justify-center gap-2">
                  {suggestions.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => submit(ex)}
                      className="rounded-full border border-border bg-surface px-3 py-1 text-xs text-ink hover:border-primary hover:text-primary"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((turn, i) => (
              <div key={i} className="space-y-3">
                {/* patient */}
                <div className="flex justify-end">
                  <div className="flex max-w-[80%] items-start gap-2">
                    <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2 text-sm text-primary-fg">
                      {turn.question}
                    </div>
                    <span className="mt-1 flex h-7 w-7 items-center justify-center rounded-full bg-primary-muted text-primary">
                      <User className="h-4 w-4" />
                    </span>
                  </div>
                </div>
                {/* agent */}
                <div className="flex justify-start">
                  <div className="flex max-w-[85%] items-start gap-2">
                    <span className="mt-1 flex h-7 w-7 items-center justify-center rounded-full bg-canvas text-ink">
                      <Stethoscope className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      {turn.result ? (
                        <ConsoleAnswerPanel result={turn.result} />
                      ) : (
                        <ThinkingBubble />
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
            <div ref={endRef} />
          </div>

          {error && <p className="mt-3 text-sm text-escalate">{error}</p>}

          {/* persistent suggestion chips — stay available after the first question */}
          {turns.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {suggestions.map((ex) => (
                <button
                  key={ex}
                  onClick={() => submit(ex)}
                  disabled={loading}
                  className="rounded-full border border-border bg-surface px-3 py-1 text-xs text-muted hover:border-primary hover:text-primary disabled:opacity-50"
                >
                  {ex}
                </button>
              ))}
            </div>
          )}

          {/* composer */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(input);
            }}
            className="mt-4 flex gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type the patient's question…"
              className="flex-1 rounded-lg border border-border bg-surface px-4 py-2 text-sm outline-none focus:border-primary"
            />
            <Button type="submit" disabled={loading}>
              {loading ? "Routing…" : "Ask"} <Send className="h-4 w-4" />
            </Button>
          </form>
        </section>

        {/* trace panel */}
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <div className="rounded-2xl border border-border bg-surface p-5 shadow-soft">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">Agent trace</h2>
              {loading ? (
                <span className="flex items-center gap-1.5 text-xs font-medium text-primary">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-primary" /> evaluating…
                </span>
              ) : active ? (
                <VerdictPill verdict={active.verdict} />
              ) : null}
            </div>
            {loading ? (
              <LivePipeline />
            ) : active ? (
              <>
                <p className="mb-2 text-[11px] text-muted">Click a step to see its JSON.</p>
                <ExpandableTrace result={active} />
                <div className="mt-4 grid grid-cols-2 gap-2 border-t border-border pt-3 text-xs">
                  <div>
                    <span className="text-muted">Confidence</span>{" "}
                    <span className="font-medium text-ink">
                      {Math.round(active.confidence * 100)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-muted">Risk</span>{" "}
                    <span className="font-medium text-ink">
                      {Math.round(active.risk * 100)}%
                    </span>
                  </div>
                </div>
                <details className="mt-3 rounded-lg border border-border bg-canvas">
                  <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-muted hover:text-ink">
                    More details (raw JSON)
                  </summary>
                  <pre className="max-h-72 overflow-auto px-3 pb-3 text-[10px] leading-relaxed text-ink">
                    {JSON.stringify(active, null, 2)}
                  </pre>
                </details>
              </>
            ) : (
              <p className="text-sm text-muted">
                The 7-agent route appears here: triage → retrieve → reason → verify → gate.
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
