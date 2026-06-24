import { cn } from "@/lib/cn";
import type { FactRecord } from "@/lib/api";

// UTC date — deterministic across server/client render.
function fmtDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

// The temporal lane: every fact the patient has had, newest first, with current
// (green) vs superseded (hollow) marked — the half-open validity made visible.
export function Timeline({ items }: { items: FactRecord[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-muted">No history yet — nothing has been recorded.</p>;
  }
  const sorted = [...items].sort((a, b) => b.effective_from.localeCompare(a.effective_from));

  return (
    <ol className="relative space-y-0">
      {sorted.map((fact, i) => {
        const isLast = i === sorted.length - 1;
        return (
          <li key={fact.id} className="relative flex gap-3 pb-4">
            {!isLast && (
              <span className="absolute left-[7px] top-4 h-full w-px bg-border" aria-hidden />
            )}
            <span
              className={cn(
                "relative z-10 mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2",
                fact.current ? "border-answer bg-answer" : "border-border bg-transparent",
              )}
            />
            <div className={cn("min-w-0 flex-1", !fact.current && "opacity-70")}>
              <div className="flex items-center gap-2">
                <span className="text-sm text-ink">{fact.summary}</span>
                {fact.current && (
                  <span className="rounded-full bg-answer-bg px-1.5 py-0.5 text-[10px] font-semibold text-answer">
                    current
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-[11px] text-muted">
                {fmtDate(fact.effective_from)} – {fact.superseded_at ? fmtDate(fact.superseded_at) : "present"} · {fact.kind}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
