import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Verdict } from "@/lib/api";

const STYLES: Record<Verdict, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  answer: { label: "Answer", cls: "bg-answer-bg text-answer", Icon: CheckCircle2 },
  clarify: { label: "Clarify", cls: "bg-clarify-bg text-clarify", Icon: HelpCircle },
  escalate: { label: "Escalate", cls: "bg-escalate-bg text-escalate", Icon: AlertTriangle },
};

export function VerdictPill({ verdict, className }: { verdict: Verdict; className?: string }) {
  const { label, cls, Icon } = STYLES[verdict];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide",
        cls,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </span>
  );
}
