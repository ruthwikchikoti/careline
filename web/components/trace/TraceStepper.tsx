import { cn } from "@/lib/cn";
import type { StepStatus, TraceStep, Verdict } from "@/lib/api";

// Map each backend rail/gate to the agent node that owns it (for display).
const AGENT_OF: Record<string, string> = {
  red_flag_rail: "Triage",
  multi_condition_tripwire: "Triage",
  reasoner: "Reasoner",
  verifier: "Verifier",
  scope_gate: "Gatekeeper",
  risk_gate: "Gatekeeper",
  cross_condition_gate: "Gatekeeper",
  confidence_staleness_gate: "Gatekeeper",
  independent_verification_gate: "Verifier",
  final_verdict: "Decision",
};

function humanize(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function dotClass(status: StepStatus, verdict: Verdict): string {
  if (status === "pass") return "bg-answer border-answer";
  if (status === "skipped") return "bg-transparent border-border";
  // terminal → tone by the turn's verdict
  return verdict === "clarify" ? "bg-clarify border-clarify" : "bg-escalate border-escalate";
}

export function TraceStepper({ steps, verdict }: { steps: TraceStep[]; verdict: Verdict }) {
  if (steps.length === 0) {
    return <p className="text-sm text-muted">No trace yet — ask a question.</p>;
  }
  return (
    <ol className="relative space-y-0">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1;
        const skipped = step.status === "skipped";
        return (
          <li key={i} className="relative flex gap-3 pb-4">
            {!isLast && (
              <span className="absolute left-[7px] top-4 h-full w-px bg-border" aria-hidden />
            )}
            <span
              className={cn(
                "relative z-10 mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2",
                dotClass(step.status, verdict),
              )}
            />
            <div className={cn("min-w-0 flex-1", skipped && "opacity-45")}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-ink">{humanize(step.name)}</span>
                <span className="rounded-full bg-canvas px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                  {AGENT_OF[step.name] ?? "Agent"}
                </span>
                {step.status === "terminal" && (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-escalate">
                    terminal
                  </span>
                )}
              </div>
              {step.detail && !skipped ? (
                <p className="mt-0.5 text-xs leading-relaxed text-muted">{step.detail}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
