"""AuditService under threadpool concurrency (SECURITY-2).

``/patient/ask`` and the console run the pipeline (and its audit write) on the
threadpool, while ``DELETE /patient/history`` / DPDP erasure rebind the turn list
from other workers. Without a lock, a read-modify-write (the call's turn count) or
a list rebind racing an append silently loses audit records — an audit trail that
drops escalations is a safety defect, not a cosmetic one.

Owner: Priyanshu (scope ``eval``) — test lives with the API package (SECURITY-2).
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone

import pytest

from careline.domain.enums import Verdict
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.services.audit_service import AuditService

_THREADS = 8
_PER_THREAD = 150


def _decision() -> Decision:
    return Decision(
        verdict=Verdict.ESCALATE,
        escalation_reason="test",
        confidence=0.1,
        risk=0.9,
        trace=ReasoningTrace(),
    )


@pytest.fixture(autouse=True)
def _aggressive_switching():
    """Force frequent GIL hand-offs so races surface deterministically-ish."""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def _run_all(targets) -> None:
    errors: list[BaseException] = []
    start = threading.Barrier(len(targets))

    def wrap(fn):
        def runner():
            try:
                start.wait()
                fn()
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        return runner

    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_audit_service_exposes_a_lock():
    audit = AuditService()
    lock = getattr(audit, "_lock", None)
    assert lock is not None and hasattr(lock, "acquire") and hasattr(lock, "release")


def test_concurrent_log_turn_never_loses_a_call_turn_count():
    audit = AuditService()
    audit.log_call(call_id="c1", patient_id="p1", doctor_id="dr-A")
    decision = _decision()

    def worker():
        for _ in range(_PER_THREAD):
            audit.log_turn(
                call_id="c1",
                patient_id="p1",
                doctor_id="dr-A",
                question="q",
                decision=decision,
            )

    _run_all([worker] * _THREADS)
    assert len(audit.turns) == _THREADS * _PER_THREAD
    call = audit.get_call("c1")
    assert call is not None
    assert call.turn_count == _THREADS * _PER_THREAD


def test_clear_and_redact_never_drop_another_patients_turns():
    """Rebinding the list for patient Y must not lose concurrent appends for X."""
    audit = AuditService()
    decision = _decision()
    for _ in range(50):
        audit.log_turn(
            call_id="cy", patient_id="py", doctor_id="dr-A", question="q", decision=decision
        )

    def writer():
        for _ in range(_PER_THREAD):
            audit.log_turn(
                call_id="cx", patient_id="px", doctor_id="dr-A", question="q", decision=decision
            )

    def clearer():
        for _ in range(_PER_THREAD):
            audit.clear_patient(doctor_id="dr-A", patient_id="py")

    def redactor():
        for _ in range(_PER_THREAD):
            audit.redact_patient("py", doctor_id="dr-A")

    def resolver():
        for t in list(audit.turns)[:_PER_THREAD]:
            audit.resolve_escalation(
                turn_id=t.turn_id,
                reply_text="r",
                resolved_by="dr-A",
                resolved_at=datetime.now(timezone.utc),
            )
            audit.resolutions_for_patient(doctor_id="dr-A", patient_id="px")

    _run_all([writer, writer, writer, clearer, redactor, resolver, writer])
    mine = audit.turns_for_patient(doctor_id="dr-A", patient_id="px")
    assert len(mine) == 4 * _PER_THREAD
    # Patient X's text was never redacted by Y's erasure (tenant/patient keyed).
    assert all(t.question == "q" and not t.redacted for t in mine)
    assert audit.turns_for_patient(doctor_id="dr-A", patient_id="py") == []
