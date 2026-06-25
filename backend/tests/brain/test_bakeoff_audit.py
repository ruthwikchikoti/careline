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


class TestAuditRedaction:
    def test_redact_patient_nulls_clinical_text_keeps_skeleton(self):
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
        count = audit.redact_patient("p-1")
        assert count >= 1
        turn = audit.turns_for_patient("p-1")[0]
        assert turn.redacted
        assert turn.question is None
        assert turn.answer_text is None
        assert turn.verdict is Verdict.ANSWER
        assert turn.turn_id  # skeleton retained

        digest = DigestService(audit).build_call_digest("call-1")
        assert "redacted" in digest.lower()


class TestEvalRerun:
    def test_offline_eval_rerun_all_pass(self):
        from careline.services.eval_rerun import rerun_offline_eval

        results, digest = rerun_offline_eval()
        assert len(results) == 8  # full T1–T8 bake-off runs live
        assert all(ok for _, _, ok in results)
        assert "8/8" in digest
