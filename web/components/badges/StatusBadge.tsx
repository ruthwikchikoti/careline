import { CheckCircle2, Clock3, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export type ResultStatus = "pass" | "fail" | "not-run";

const STATUS = {
  pass: {
    label: "Pass",
    className: "bg-answer-bg text-answer",
    Icon: CheckCircle2,
  },
  fail: {
    label: "Fail",
    className: "bg-escalate-bg text-escalate",
    Icon: XCircle,
  },
  "not-run": {
    label: "Not run",
    className: "bg-canvas text-muted",
    Icon: Clock3,
  },
} satisfies Record<ResultStatus, { label: string; className: string; Icon: typeof CheckCircle2 }>;

export function StatusBadge({
  status,
  className,
}: {
  status: ResultStatus;
  className?: string;
}) {
  const { label, className: statusClassName, Icon } = STATUS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold uppercase tracking-wide",
        statusClassName,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </span>
  );
}
