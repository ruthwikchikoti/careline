"""AuditService + DigestService tests (VI-7).

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from careline.domain.enums import Verdict
from careline.domain.model.decision import Decision
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.digest_service import DigestService

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


class TestAuditService:
    def test_log_turn_and_call(self):
        audit = AuditService()
        audit.log_call(
            call_id="call-1",
            patient_id="p-1",
            doctor_id="dr-1",
            started_at=_NOW,
        )
        decision = Decision.answer(
            "Take Paracetamol as prescribed.",
            confidence=0.9,
            risk=0.1,
        )
        record = audit.log_turn(
            call_id="call-1",
            patient_id="p-1",
            doctor_id="dr-1",
            question="paracetamol dose?",
            decision=decision,
            logged_at=_NOW,
        )
        assert record.verdict is Verdict.ANSWER
        assert record.question == "paracetamol dose?"
        call = audit.get_call("call-1")
        assert call is not None
        assert call.turn_count == 1

    def test_log_event(self):
        audit = AuditService()
        event = audit.log_event(
            AuditEventKind.CONSENT,
            patient_id="p-1",
            detail="recording consent granted",
            logged_at=_NOW,
        )
        assert event.kind is AuditEventKind.CONSENT
        assert len(audit.events) == 1


class TestDigestService:
    def test_call_digest_includes_turns(self):
        audit = AuditService()
        audit.log_call(call_id="call-1", patient_id="p-1", doctor_id="dr-1", started_at=_NOW)
        audit.log_turn(
            call_id="call-1",
            patient_id="p-1",
            doctor_id="dr-1",
            question="paracetamol dose?",
            decision=Decision.answer("Paracetamol 500mg.", confidence=0.9),
            logged_at=_NOW,
        )
        digest = DigestService(audit).build_call_digest("call-1")
        assert "call-1" in digest
        assert "paracetamol" in digest

    def test_daily_digest(self):
        audit = AuditService()
        audit.log_call(call_id="call-1", patient_id="p-1", doctor_id="dr-1", started_at=_NOW)
        digest = DigestService(audit).build_daily_digest("dr-1", date(2026, 6, 21))
        assert "dr-1" in digest
        assert "call-1" in digest
