"""DpdpService unit tests (NR-7).

Pins the 3-layer erase orchestration: ownership check, Layer-1 soft-delete,
Layer-2 forget, audit redaction, and ERASURE audit event.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from careline.adapters.memory.local import LocalMemoryProvider
from careline.adapters.memory.seed import seed_patient
from careline.domain.model.patient import Patient
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.dpdp_service import DpdpService, ErasedNothing

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


class _PatientRepoSpy:
    def __init__(self, patient: Patient | None) -> None:
        self._patient = patient
        self.soft_delete_calls: list[tuple[str, str]] = []

    async def get(self, *, doctor_id: str, patient_id: str) -> Patient | None:
        if self._patient is None:
            return None
        if (
            self._patient.doctor_id != doctor_id
            or self._patient.patient_id != patient_id
        ):
            return None
        return self._patient

    async def soft_delete(self, *, doctor_id: str, patient_id: str) -> int:
        self.soft_delete_calls.append((doctor_id, patient_id))
        return 3


class _MemorySpy(LocalMemoryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.forget_calls: list[tuple[str, str]] = []

    async def forget(self, *, doctor_id: str, patient_id: str) -> None:
        self.forget_calls.append((doctor_id, patient_id))
        await super().forget(doctor_id=doctor_id, patient_id=patient_id)


class _AuditSpy(AuditService):
    def __init__(self) -> None:
        super().__init__()
        self.redact_calls: list[str] = []

    def redact_patient(self, patient_id: str) -> int:
        self.redact_calls.append(patient_id)
        return super().redact_patient(patient_id)


def _run(coro):
    return asyncio.run(coro)


def _svc(
    *,
    patient: Patient | None = None,
) -> tuple[DpdpService, _PatientRepoSpy, _MemorySpy, _AuditSpy]:
    repo = _PatientRepoSpy(patient)
    memory = _MemorySpy()
    audit = _AuditSpy()
    return DpdpService(patient_repo=repo, memory=memory, audit=audit), repo, memory, audit


def test_erase_calls_layers_in_order():
    patient = seed_patient()
    svc, repo, memory, audit = _svc(patient=patient)
    _run(
        svc.erase(
            doctor_id=patient.doctor_id,
            patient_id=patient.patient_id,
        )
    )
    assert repo.soft_delete_calls == [(patient.doctor_id, patient.patient_id)]
    assert memory.forget_calls == [(patient.doctor_id, patient.patient_id)]
    assert audit.redact_calls == [patient.patient_id]


def test_erase_returns_layer_counts():
    patient = seed_patient()
    svc, _, _, audit = _svc(patient=patient)
    audit.log_turn(
        call_id="call-1",
        patient_id=patient.patient_id,
        doctor_id=patient.doctor_id,
        question="dose?",
        decision=__import__(
            "careline.domain.model.decision", fromlist=["Decision"]
        ).Decision.answer("500mg", confidence=0.9),
        logged_at=_NOW,
    )
    result = _run(
        svc.erase(
            doctor_id=patient.doctor_id,
            patient_id=patient.patient_id,
        )
    )
    assert result.patient_id == patient.patient_id
    assert result.layer1_nulled == 3
    assert result.layer2_dropped is True
    assert result.audit_redacted >= 1


def test_erase_raises_erased_nothing_when_patient_missing():
    svc, _, _, _ = _svc(patient=None)
    with pytest.raises(ErasedNothing):
        _run(svc.erase(doctor_id="dr-A", patient_id="missing"))


def test_erase_logs_erasure_audit_event():
    patient = seed_patient()
    svc, _, _, audit = _svc(patient=patient)
    _run(
        svc.erase(
            doctor_id=patient.doctor_id,
            patient_id=patient.patient_id,
        )
    )
    erasure_events = [
        e for e in audit.events if e.kind is AuditEventKind.ERASURE
    ]
    assert len(erasure_events) >= 1
    assert any(e.doctor_id == patient.doctor_id for e in erasure_events)
    assert any(e.patient_id == patient.patient_id for e in erasure_events)


class TestInterfaceDriftGuard:
    """Frozen wire shapes — fail if an interface owner renames a field."""

    def test_question_in_fields_stable(self):
        from careline.api.dto.brain import QuestionIn

        assert set(QuestionIn.model_fields) == {
            "doctor_id",
            "patient_id",
            "call_id",
            "question",
        }

    def test_answer_out_fields_stable(self):
        from careline.api.dto.brain import AnswerOut

        assert set(AnswerOut.model_fields) == {
            "verdict",
            "answer_text",
            "escalation_reason",
            "confidence",
            "risk",
            "citations",
            "trace",
        }

    def test_erasure_out_fields_stable(self):
        from careline.api.dto.patients import ErasureOut

        assert set(ErasureOut.model_fields) == {
            "patient_id",
            "layer1_nulled",
            "layer2_dropped",
            "audit_redacted",
        }

    def test_escalation_payload_fields_stable(self):
        from careline.adapters.telephony.stub import EscalationPayload

        assert set(EscalationPayload.model_fields) == {
            "call_id",
            "patient_id",
            "doctor_id",
            "reason",
            "escalated_at",
            "terminal_gate",
        }

    def test_valid_slice_fields_stable(self):
        from careline.domain.model.patient import ValidSlice

        assert set(ValidSlice.model_fields) == {"as_of", "facts"}

    def test_decision_fields_stable(self):
        from careline.domain.model.decision import Decision

        assert set(Decision.model_fields) == {
            "verdict",
            "answer_text",
            "escalation_reason",
            "scope",
            "confidence",
            "risk",
            "citations",
            "trace",
        }
