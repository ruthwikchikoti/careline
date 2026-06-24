"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { approveFacts, type ApprovalOut } from "@/lib/api";
import { Button } from "@/components/ui/Button";

interface ApproveButtonProps {
  consultationId: string;
  factCount: number;
  disabled?: boolean;
  onApproved: (result: ApprovalOut) => void;
}

export function ApproveButton({
  consultationId,
  factCount,
  disabled = false,
  onApproved,
}: ApproveButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove() {
    setError(null);
    setLoading(true);
    try {
      const result = await approveFacts(consultationId);
      onApproved(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-2">
      <Button onClick={handleApprove} disabled={disabled || loading || factCount === 0}>
        {loading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Approving…
          </>
        ) : (
          `Approve ${factCount} fact${factCount === 1 ? "" : "s"}`
        )}
      </Button>
      {error ? <p className="text-sm text-escalate">{error}</p> : null}
    </div>
  );
}
