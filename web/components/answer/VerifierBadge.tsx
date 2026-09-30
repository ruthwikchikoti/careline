import { ShieldAlert, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/cn";
import type { TraceStep, Verdict } from "@/lib/api";

// The independent Verifier's affirmation, derived from the reasoning trace. An ANSWER
// can only exist if the independent-verification gate passed, so for an answer we show
// a green "Independently verified"; otherwise we surface that the verifier did not
// confirm (the veto that kept an unsupported candidate from being answered).
export function VerifierBadge({
  steps,
  verdict,
}: {
  steps: TraceStep[];
  verdict: Verdict;
}) {
  const verifyStep =
    steps.find((s) => s.name === "independent_verification_gate") ??
    steps.find((s) => s.name === "verifier");
  const affirmed = verdict === "answer";
  const Icon = affirmed ? ShieldCheck : ShieldAlert;
  const label = affirmed ? "Independently verified" : "Not verifier-confirmed";
  const tone = affirmed ? "bg-answer-bg text-answer" : "bg-clarify-bg text-clarify";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
        tone,
      )}
      title={verifyStep?.detail ?? undefined}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </span>
  );
}
