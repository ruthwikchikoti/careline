import { Siren } from "lucide-react";
import { cn } from "@/lib/cn";

export function RedFlagBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-escalate-bg px-3 py-1 text-xs font-semibold uppercase tracking-wide text-redflag",
        className,
      )}
    >
      <Siren className="h-3.5 w-3.5" />
      Red flag
    </span>
  );
}
