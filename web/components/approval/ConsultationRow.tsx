import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { ConsultationOut } from "@/lib/api";
import { cn } from "@/lib/cn";

function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function StatusPill({ status }: { status: ConsultationOut["status"] }) {
  const approved = status === "approved";
  const archived = status === "archived";
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-xs font-medium capitalize",
        approved && "bg-answer-bg text-answer",
        !approved && !archived && "bg-clarify-bg text-clarify",
        archived && "bg-canvas text-muted",
      )}
    >
      {status}
    </span>
  );
}

export function ConsultationRow({ consultation }: { consultation: ConsultationOut }) {
  const factLabel = `${consultation.fact_count} fact${consultation.fact_count === 1 ? "" : "s"}`;

  return (
    <Link
      href={`/consultations/${consultation.consultation_id}`}
      className="flex items-center justify-between gap-3 rounded-xl border border-border p-3 transition-colors hover:bg-canvas"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink">{consultation.patient_id}</p>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
          <StatusPill status={consultation.status} />
          <span>{factLabel}</span>
          <span>{formatRelativeTime(consultation.created_at)}</span>
        </p>
      </div>
      <ArrowRight className="h-4 w-4 shrink-0 text-muted" />
    </Link>
  );
}
