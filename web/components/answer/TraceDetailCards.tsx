import { Brain, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/cn";
import type { StepStatus, TraceStep } from "@/lib/api";

// The reason/verify detail cards: what the Reasoner proposed and what the Verifier
// concluded, in their own words from the trace — the "why" behind the answer.
const DETAIL_STEPS = [
  { name: "reasoner", label: "Reasoner", Icon: Brain },
  { name: "verifier", label: "Verifier", Icon: ShieldCheck },
] as const;

function statusTone(status: StepStatus): string {
  if (status === "pass") return "text-answer";
  if (status === "terminal") return "text-escalate";
  return "text-muted";
}

export function TraceDetailCards({ steps }: { steps: TraceStep[] }) {
  const cards = DETAIL_STEPS.flatMap(({ name, label, Icon }) => {
    const step = steps.find((s) => s.name === name);
    return step ? [{ label, Icon, step }] : [];
  });
  if (cards.length === 0) return null;

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {cards.map(({ label, Icon, step }) => (
        <div key={label} className="rounded-lg border border-border bg-canvas px-3 py-2">
          <div className="flex items-center gap-1.5">
            <Icon className="h-3.5 w-3.5 text-muted" />
            <span className="text-xs font-semibold text-ink">{label}</span>
            <span className={cn("text-[10px] font-medium uppercase tracking-wide", statusTone(step.status))}>
              {step.status}
            </span>
          </div>
          {step.detail ? (
            <p className="mt-1 text-xs leading-relaxed text-muted">{step.detail}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}
