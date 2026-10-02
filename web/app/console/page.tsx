"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Phone, Send, Stethoscope, User } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { VerdictPill } from "@/components/ui/VerdictPill";
import { TraceStepper } from "@/components/trace/TraceStepper";
import { ConsoleAnswerPanel } from "./_answer/ConsoleAnswerPanel";
import {
  ask,
  getPatientRecord,
  listPatients,
  type AnswerResult,
  type FactRecord,
  type PatientOut,
} from "@/lib/api";

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

// Turn a patient's *current* approved facts into the kind of follow-up question
// they'd actually phone in about — so the suggestions reflect their real record
// instead of a hard-coded list.
function suggestionsFor(facts: FactRecord[]): string[] {
  const out: string[] = [];
  for (const f of facts) {
    const head = f.summary.split(/[\s,;:—-]/)[0]?.trim() || f.summary;
    switch (f.kind) {
      case "medication":
        out.push(`What is my ${head} dose?`);
        out.push(`Should I still take ${head}?`);
        break;
      case "instruction":
        out.push(/diet/i.test(f.summary) ? "What diet should I follow now?" : "What are my care instructions?");
        break;
      case "allergy": {
        const substance = f.summary.split(/allerg/i)[0]?.trim() || head;
        out.push(`Am I allergic to ${substance}?`);
        break;
      }
      case "diagnosis":
        out.push(`Can you tell me about my ${f.summary.replace(/\.$/, "")}?`);
        break;
      case "observation":
        out.push("What were my latest test results?");
        break;
      case "follow_up":
        out.push("When is my next appointment?");
        break;
    }
  }
  const unique = Array.from(new Set(out));
  return unique.length ? unique.slice(0, 5) : EXAMPLES;
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
    const id = patientId.trim();
    if (!id) {
      setSuggestions(EXAMPLES);
      return;
    }
    let active = true;
    getPatientRecord(id)
      .then((rec) => active && setSuggestions(suggestionsFor(rec.current)))
      .catch(() => active && setSuggestions(EXAMPLES));
    return () => {
      active = false;
    };
  }, [patientId]);

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
            {active ? (
              <TraceStepper steps={active.trace} verdict={active.verdict} />
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
