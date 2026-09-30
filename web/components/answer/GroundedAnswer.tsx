import { cn } from "@/lib/cn";
import type { AnswerResult, DemoFact } from "@/lib/api";
import { VerdictPill } from "@/components/ui/VerdictPill";
import { CitationList } from "@/components/citations/CitationList";
import { TraceDetailCards } from "./TraceDetailCards";
import { VerifierBadge } from "./VerifierBadge";

// The grounded-answer panel (UI task #6). Renders a turn's verdict with everything
// that makes the answer trustworthy and legible: the answer text, the verifier's
// affirmation, confidence/risk, the cited valid facts it is grounded in, and the
// reason/verify trace detail. For CLARIFY/ESCALATE it degrades to the verdict + reason.
function Meter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "answer" | "escalate" | "muted";
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const bar = tone === "answer" ? "bg-answer" : tone === "escalate" ? "bg-escalate" : "bg-muted";
  return (
    <div>
      <div className="flex justify-between text-[11px] font-medium text-muted">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-canvas">
        <div className={cn("h-full rounded-full", bar)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function GroundedAnswer({
  result,
  facts = [],
  className,
}: {
  result: AnswerResult;
  facts?: DemoFact[];
  className?: string;
}) {
  const isAnswer = result.verdict === "answer";
  const body = result.answer_text ?? result.escalation_reason ?? "";

  return (
    <div
      className={cn(
        "space-y-3 rounded-2xl border border-border bg-surface p-4 shadow-soft",
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <VerdictPill verdict={result.verdict} />
        <VerifierBadge steps={result.trace} verdict={result.verdict} />
      </div>

      <p className="text-sm leading-relaxed text-ink">{body}</p>

      {isAnswer && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Meter label="Confidence" value={result.confidence} tone="answer" />
            <Meter
              label="Risk"
              value={result.risk}
              tone={result.risk >= 0.5 ? "escalate" : "muted"}
            />
          </div>

          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              Grounded in
            </p>
            <CitationList citations={result.citations} facts={facts} />
          </div>
        </>
      )}

      <TraceDetailCards steps={result.trace} />
    </div>
  );
}
