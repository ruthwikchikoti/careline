import { cn } from "@/lib/cn";
import type { FactRecord } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  medication: "Medication",
  instruction: "Instruction",
  diagnosis: "Diagnosis",
  observation: "Observation",
  allergy: "Allergy",
  follow_up: "Follow-up",
};

// UTC date — deterministic across server/client render (no locale/hydration drift).
function fmtDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

// One fact in the patient's record: the doctor-approved phrasing plus its temporal
// validity and approval stamp. Superseded facts read back muted (history, not truth).
export function FactCard({ fact }: { fact: FactRecord }) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-border bg-surface p-4 shadow-soft",
        !fact.current && "opacity-70",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
          {KIND_LABEL[fact.kind] ?? fact.kind}
        </span>
        {fact.current ? (
          <span className="rounded-full bg-answer-bg px-2 py-0.5 text-[10px] font-semibold text-answer">
            Current
          </span>
        ) : (
          <span className="rounded-full bg-canvas px-2 py-0.5 text-[10px] font-semibold text-muted">
            Superseded
          </span>
        )}
      </div>

      <p className="mt-1.5 text-sm text-ink">{fact.summary}</p>

      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        From {fmtDate(fact.effective_from)}
        {fact.superseded_at ? ` · retired ${fmtDate(fact.superseded_at)}` : ""}
        {fact.approved_by ? ` · approved by ${fact.approved_by}` : " · awaiting approval"}
      </p>
    </div>
  );
}
