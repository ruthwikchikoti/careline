import { cn } from "@/lib/cn";
import type { DemoFact } from "@/lib/api";
import { CitationChip } from "./CitationChip";

// The facts an ANSWER is grounded in. Resolves each cited id against the patient's
// valid slice so the panel shows real approved phrasing, not opaque ids.
export function CitationList({
  citations,
  facts = [],
  className,
}: {
  citations: string[];
  facts?: DemoFact[];
  className?: string;
}) {
  if (citations.length === 0) {
    return <p className="text-xs text-muted">No facts cited.</p>;
  }
  const byId = new Map(facts.map((f) => [f.id, f]));
  return (
    <ul className={cn("space-y-1.5", className)}>
      {citations.map((id) => (
        <CitationChip key={id} id={id} fact={byId.get(id)} />
      ))}
    </ul>
  );
}
