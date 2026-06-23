"""Digest builder — human-readable audit summaries (VI-7).

Turns raw audit records into briefs a doctor can skim: per-call summaries,
daily digests, and eval-run reports.  Clinical text is included only while
the patient record is active; after DPDP erasure the digest shows verdict
skeletons only.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

from datetime import date, datetime

from careline.domain.enums import Verdict
from careline.services.audit_service import AuditCallRecord, AuditService, AuditTurnRecord


class DigestService:
    """Build human-readable summaries from :class:`AuditService` records."""

    def __init__(self, audit: AuditService) -> None:
        self._audit = audit

    def build_call_digest(self, call_id: str) -> str:
        """One-call summary for doctor review."""
        turns = self._audit.turns_for_call(call_id)
        if not turns:
            return f"Call {call_id}: no turns recorded."

        lines = [f"Call {call_id} — {len(turns)} turn(s)"]
        for i, turn in enumerate(turns, start=1):
            lines.append(self._format_turn(i, turn))
        return "\n".join(lines)

    def build_daily_digest(self, doctor_id: str, day: date) -> str:
        """All calls for a doctor on one calendar day."""
        calls = [
            c
            for c in self._audit.calls_for_doctor(doctor_id)
            if c.started_at.date() == day
        ]
        if not calls:
            return f"No calls for {doctor_id} on {day.isoformat()}."

        escalations = sum(1 for c in calls if c.escalated)
        lines = [
            f"Daily digest — {doctor_id} — {day.isoformat()}",
            f"Calls: {len(calls)}, escalations: {escalations}",
            "",
        ]
        for call in sorted(calls, key=lambda c: c.started_at):
            lines.append(self._format_call_line(call))
        return "\n".join(lines)

    def build_eval_digest(self, results: list[tuple[str, Verdict, bool]]) -> str:
        """Summary of an offline eval re-run (scenario, verdict, passed)."""
        passed = sum(1 for _, _, ok in results if ok)
        lines = [
            f"Eval re-run: {passed}/{len(results)} scenarios passed",
            "",
        ]
        for name, verdict, ok in results:
            status = "PASS" if ok else "FAIL"
            lines.append(f"  [{status}] {name} -> {verdict.value}")
        return "\n".join(lines)

    def _format_turn(self, index: int, turn: AuditTurnRecord) -> str:
        prefix = f"  Turn {index}: {turn.verdict.value}"
        if turn.redacted:
            return f"{prefix} (clinical text redacted)"
        if turn.verdict is Verdict.ANSWER and turn.answer_text:
            return f"{prefix} — Q: {turn.question!r} -> A: {turn.answer_text!r}"
        if turn.verdict is Verdict.ESCALATE and turn.escalation_reason:
            return f"{prefix} — Q: {turn.question!r} — {turn.escalation_reason}"
        if turn.verdict is Verdict.CLARIFY and turn.answer_text:
            return f"{prefix} — Q: {turn.question!r} — clarify: {turn.answer_text!r}"
        return f"{prefix} — Q: {turn.question!r}"

    def _format_call_line(self, call: AuditCallRecord) -> str:
        verdict = call.final_verdict.value if call.final_verdict else "in-progress"
        flag = " [ESCALATED]" if call.escalated else ""
        redacted = " [REDACTED]" if call.redacted else ""
        return (
            f"  {call.call_id} patient={call.patient_id} "
            f"turns={call.turn_count} verdict={verdict}{flag}{redacted}"
        )


__all__ = ["DigestService"]
