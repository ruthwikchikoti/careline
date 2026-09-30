"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowLeft, Phone, Send, Stethoscope, User } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { VerdictPill } from "@/components/ui/VerdictPill";
import { TraceStepper } from "@/components/trace/TraceStepper";
import { ConsoleAnswerPanel } from "./_answer/ConsoleAnswerPanel";
import { ask, type AnswerResult } from "@/lib/api";

interface Turn {
  question: string;
  result: AnswerResult;
}

const EXAMPLES = [
  "soft diet post surgery",
  "should I take amoxicillin",
  "Can I eat sweets post-surgery given my diabetes?",
  "I have chest pain",
];

export default function ConsolePage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const active = turns[turns.length - 1]?.result;

  async function submit(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    setLoading(true);
    setError(null);
    try {
      const result = await ask(q);
      setTurns((t) => [...t, { question: q, result }]);
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 0);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
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
            <p className="text-sm font-semibold text-ink">Live call · Ravi K. (demo-patient)</p>
            <p className="text-xs text-muted">Answers only from approved, currently-valid context</p>
          </div>
        </div>
        <span className="hidden items-center gap-1.5 text-xs font-medium text-answer sm:flex">
          <span className="h-2 w-2 rounded-full bg-answer" /> on the line
        </span>
      </header>

      <div className="mx-auto grid w-full max-w-6xl flex-1 grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_360px]">
        {/* conversation */}
        <section className="flex flex-col">
          <div className="flex-1 space-y-4">
            {turns.length === 0 && (
              <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted">
                Ask a follow-up question as the patient. Try one:
                <div className="mt-3 flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map((ex) => (
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
                      <ConsoleAnswerPanel result={turn.result} />
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
              {active && <VerdictPill verdict={active.verdict} />}
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
