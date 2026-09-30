import { FileText } from "lucide-react";
import type { DemoFact } from "@/lib/api";

// One cited fact. When the fact is resolved from the patient's valid slice we show
// the doctor-approved summary (the phrasing the answer is grounded in); otherwise we
// fall back to the bare fact id so a citation is never silently dropped.
export function CitationChip({ id, fact }: { id: string; fact?: DemoFact }) {
  return (
    <li className="flex items-start gap-2 rounded-lg border border-border bg-canvas px-3 py-2">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-answer-bg text-answer">
        <FileText className="h-3 w-3" />
      </span>
      <div className="min-w-0">
        {fact ? (
          <>
            <p className="text-sm text-ink">{fact.summary}</p>
            <p className="mt-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
              {fact.kind} · <span className="font-mono normal-case">{id}</span>
            </p>
          </>
        ) : (
          <p className="font-mono text-xs text-muted">{id}</p>
        )}
      </div>
    </li>
  );
}
