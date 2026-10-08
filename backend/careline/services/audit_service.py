"""Audit logging — turn/call/event records for observability (VI-7).

Every live turn is logged with enough structure to reconstruct *what* happened
(which verdict, which gate fired) without retaining clinical text longer than
necessary.  :meth:`AuditService.redact_patient` implements DPDP erasure: clinical
text is nulled but the audit skeleton (ids, timestamps, verdicts, trace steps)
is retained for compliance.

Doctor REVIEW queue: only ESCALATE pages the doctor, so a CLARIFY
redirect on a real emergency (8/50 on blind battery 2) never reached a human.
:func:`review_reason` flags a CLARIFY turn whose question mentions a danger
concept, reads as a current symptom report, or is OUT_OF_SCOPE with a
first-person / carer subject plus body-or-symptom words. Flagged turns carry
``needs_review=True`` and surface in the doctor's queue. This never changes a
verdict — it is a second, human net on top of the spine.

Owner: Priyanshu (scope ``eval``).
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory, Verdict
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.rails.symptom_report import (
    check_symptom_report,
    mentions_danger_concept,
)

# --- doctor review flag -------------------------------------------------------
# Deliberately broad: a false flag costs the doctor a glance; a missed one leaves
# a redirected emergency unseen. Subject = the caller or someone they care for
# (English + transliterated Hindi). Body/symptom = a wide lexicon, not a danger
# list — the danger lists are the rails, and these turns already slipped past them.
_REVIEW_SUBJECT_RE = re.compile(
    r"\b(?:i|i'?m|im|i'?ve|ive|i'?d|me|my|myself|we|our|us|"
    r"he|he'?s|she|she'?s|his|her|him|they|their|"
    r"son'?s?|daughter'?s?|baby'?s?|child'?s?|kid'?s?|infant|toddler|newborn|"
    r"wife'?s?|husband'?s?|mum'?s?|mom'?s?|mother'?s?|dad'?s?|father'?s?|"
    r"grandma'?s?|grandmother'?s?|grandpa'?s?|grandfather'?s?|granny|nani|dadi|"
    r"brother'?s?|sister'?s?|parent'?s?|patient|"
    r"mujhe|mujh|mera|meri|mere|main|maine|hum|humara|hamara|"
    r"bachh?a|bachh?e|bachh?i|beta|beti|papa|mummy|maa)\b",
    re.IGNORECASE,
)
_REVIEW_BODY_RE = re.compile(
    r"\b(?:pain\w*|hurt\w*|ach(?:e|es|ing|y)|sore|swell\w*|swollen|bleed\w*|blood\w*|"
    r"fever\w*|temperature|hot to (?:the )?touch|rash\w*|itch\w*|hives|"
    r"vomit\w*|nause\w*|diarr?h\w*|dizz\w*|faint\w*|light-?headed|"
    r"breath\w*|wheez\w*|cough\w*|chok\w*|asthma|puffs?|inhaler|"
    r"chest|heart\w*|palpitat\w*|head\w*|stomach|belly|tummy|abdom\w*|"
    r"sleepy|drows\w*|confus\w*|floppy|limp|weak\w*|numb\w*|tingl\w*|"
    r"seiz\w*|fits?|unconscious|passed out|collaps\w*|wak(?:e|ing)|"
    r"feed\w*|not eating|sting\w*|bit(?:e|ten)|allerg\w*|tongue|throat|lips?|"
    r"swallow\w*|vision|blurr\w*|eyes?|fell|fall(?:en)?|banged|injur\w*|burn\w*|"
    r"wound|stitch\w*|incision|pills?|tablets?|overdos\w*|drinks?|drank|poison\w*|"
    r"kerosene|bleach|swallowed|weird|slow|"
    r"suicid\w*|kill (?:my|him|her)sel\w*|end (?:it|my life)|die|dying|"
    r"jeen[ae]|marn[ae]|khatam|dard|bukhar|chakkar|saans|ulti|khoon|behosh|"
    r"pi liya|kha liya|nigal)\b",
    re.IGNORECASE,
)

# No explicit subject but a present-tense report ("Bee sting ... now dizzy").
_REVIEW_PRESENT_RE = re.compile(r"\b(?:now|right now|abhi|since)\b", re.IGNORECASE)


def review_reason(question: str | None, decision: Decision) -> str | None:
    """Why a redirected (CLARIFY) turn needs a doctor's eyes, or ``None``.

    Only CLARIFY turns are review items: ESCALATE already reaches the doctor and
    ANSWER is not a redirect. Fails toward flagging — a rail error flags the turn.
    """
    if decision.verdict is not Verdict.CLARIFY or not question:
        return None
    try:
        danger = mentions_danger_concept(question)
        if danger is not None:
            return f"danger concept: {danger}"
        symptom = check_symptom_report(question, context=False)
        if symptom is not None:
            return f"symptom report: {symptom}"
    except Exception:  # noqa: BLE001 - fail closed: a broken rail flags, never hides
        return "review rail error"
    if decision.scope in (ScopeCategory.OUT_OF_SCOPE, None) and (
        _REVIEW_SUBJECT_RE.search(question) or _REVIEW_PRESENT_RE.search(question)
    ):
        body = _REVIEW_BODY_RE.search(question)
        if body is not None:
            return f"redirected symptom words: {body.group(0).lower()}"
    return None


class AuditEventKind(str, Enum):
    """Categories of audit events beyond turn/call boundaries."""

    SYSTEM = "system"
    ESCALATION = "escalation"
    CONSENT = "consent"
    ERASURE = "erasure"
    EVAL = "eval"


class AuditTurnRecord(BaseModel):
    """One logged question turn — the primary audit unit."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    call_id: str
    patient_id: str
    doctor_id: str
    logged_at: datetime
    verdict: Verdict
    question: str | None = None
    answer_text: str | None = None
    escalation_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    trace_steps: list[dict[str, Any]] = Field(default_factory=list)
    redacted: bool = False
    # Defaults keep rows written before the review queue loadable (Mongo hydrate).
    scope: str | None = None
    needs_review: bool = False
    review_reason: str | None = None


class AuditCallRecord(BaseModel):
    """Summary record for an entire call."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    patient_id: str
    doctor_id: str
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    final_verdict: Verdict | None = None
    escalated: bool = False
    redacted: bool = False


class AuditEventRecord(BaseModel):
    """Generic audit event (consent, erasure, eval run, etc.)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    kind: AuditEventKind
    logged_at: datetime
    patient_id: str | None = None
    doctor_id: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditResolutionRecord(BaseModel):
    """A doctor's reply that closes an escalated turn — the human-in-the-loop answer.

    Keyed to the escalated ``turn_id`` so the patient can be shown the doctor's
    response to the exact question they asked. This is the loop-closing record:
    the agent handed off, the doctor answered, and that answer is now durable.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    patient_id: str
    doctor_id: str
    reply_text: str
    resolved_by: str
    resolved_at: datetime


class AuditFeedbackRecord(BaseModel):
    """A patient's helpful / not-helpful rating of one of their own turns.

    Human online evaluation (patient channel). One per turn — re-rating
    overwrites. ``comment`` is free text from the patient, so it is PHI-adjacent:
    it lives only here (tenant-scoped, nulled by DPDP redaction), never in the
    process-wide monitor.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    patient_id: str
    doctor_id: str
    verdict: Verdict
    helpful: bool
    comment: str | None = None
    rated_at: datetime


class AuditReviewRecord(BaseModel):
    """A doctor's correct / incorrect review of one turn (expert human eval).

    One per turn — re-review overwrites. ``note`` stays in the tenant-scoped audit
    store only (nulled by DPDP redaction); the monitor gets just the boolean.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    patient_id: str
    doctor_id: str
    verdict: Verdict
    correct: bool
    note: str | None = None
    reviewed_by: str
    reviewed_at: datetime


#: Verdicts a patient may rate — an escalation is not an AI answer to judge.
RATABLE_VERDICTS = frozenset({Verdict.ANSWER, Verdict.CLARIFY})


class NotRatableError(ValueError):
    """The turn exists and is the caller's, but its verdict cannot be rated."""


def _trace_to_skeleton(trace: ReasoningTrace) -> list[dict[str, Any]]:
    """Serialise trace steps without clinical content — safe for redacted logs."""
    return [
        {
            "name": step.name,
            "status": step.status.value,
            "spec_section": step.spec_section,
            "detail": step.detail,
        }
        for step in trace.steps
    ]


class AuditStore(Protocol):
    """Durable persistence seam for the audit trail (see mongo/audit_store.py).

    The service keeps an in-memory read model; an optional store makes that model
    restart-survivable by hydrating it on construction and receiving every write.
    Kept as a Protocol so the offline suite needs no store at all (``None``).
    """

    def save_turn(self, record: AuditTurnRecord) -> None: ...
    def save_call(self, record: AuditCallRecord) -> None: ...
    def save_event(self, record: AuditEventRecord) -> None: ...
    def save_resolution(self, record: AuditResolutionRecord) -> None: ...
    def load(
        self,
    ) -> tuple[
        list[AuditTurnRecord],
        list[AuditCallRecord],
        list[AuditEventRecord],
        list[AuditResolutionRecord],
    ]: ...

    # Optional (human feedback): ``save_feedback(record)``, ``save_review(record)``
    # and ``load_human_feedback() -> (feedback, reviews)``. Detected with hasattr so
    # a store predating them keeps working (feedback is then in-memory only).


class AuditService:
    """In-memory audit read model, optionally write-through to a durable store.

    With no ``store`` (offline/tests) it is pure in-memory, exactly as before.
    With a store (Mongo), the in-memory model is hydrated on startup and mirrored
    on every write, so the audit trail survives restarts.

    Thread-safe (SECURITY-2): turns are logged from threadpool workers while
    other workers clear/redact, so every mutation, list/dict rebind and
    read-modify-write (a call's turn count) runs under one re-entrant lock, and
    reads take a snapshot under it. Durable write-through happens *outside* the
    lock so a slow store never serialises live turns.
    """

    def __init__(self, *, store: AuditStore | None = None) -> None:
        self._lock = threading.RLock()
        self._turns: list[AuditTurnRecord] = []
        self._calls: dict[str, AuditCallRecord] = {}
        self._events: list[AuditEventRecord] = []
        self._resolutions: dict[str, AuditResolutionRecord] = {}
        self._feedback: dict[str, AuditFeedbackRecord] = {}
        self._reviews: dict[str, AuditReviewRecord] = {}
        self._store = store
        if store is not None:
            turns, calls, events, resolutions = store.load()
            self._turns = list(turns)
            self._calls = {c.call_id: c for c in calls}
            self._events = list(events)
            self._resolutions = {r.turn_id: r for r in resolutions}
            if hasattr(store, "load_human_feedback"):
                try:
                    feedback, reviews = store.load_human_feedback()
                    self._feedback = {f.turn_id: f for f in feedback}
                    self._reviews = {r.turn_id: r for r in reviews}
                except Exception:  # noqa: BLE001 - feedback is advisory; audit must load
                    pass

    def _persist_turn(self, record: AuditTurnRecord) -> None:
        """Best-effort write-through — a storage hiccup never breaks a live turn."""
        if self._store is not None:
            try:
                self._store.save_turn(record)
            except Exception:  # noqa: BLE001 - durability is best-effort, answer must return
                pass

    def _persist_call(self, record: AuditCallRecord) -> None:
        if self._store is not None:
            try:
                self._store.save_call(record)
            except Exception:  # noqa: BLE001
                pass

    def _persist_event(self, record: AuditEventRecord) -> None:
        if self._store is not None:
            try:
                self._store.save_event(record)
            except Exception:  # noqa: BLE001
                pass

    def _persist_resolution(self, record: AuditResolutionRecord) -> None:
        if self._store is not None:
            try:
                self._store.save_resolution(record)
            except Exception:  # noqa: BLE001
                pass

    def _persist_optional(self, method: str, record: BaseModel) -> None:
        """Best-effort write-through to an optional store method (human feedback)."""
        if self._store is not None and hasattr(self._store, method):
            try:
                getattr(self._store, method)(record)
            except Exception:  # noqa: BLE001
                pass

    @property
    def turns(self) -> tuple[AuditTurnRecord, ...]:
        with self._lock:
            return tuple(self._turns)

    @property
    def events(self) -> tuple[AuditEventRecord, ...]:
        with self._lock:
            return tuple(self._events)

    def log_turn(
        self,
        *,
        call_id: str,
        patient_id: str,
        doctor_id: str,
        question: str,
        decision: Decision,
        logged_at: datetime | None = None,
    ) -> AuditTurnRecord:
        """Record one question turn and its terminal decision."""
        reason = review_reason(question, decision)
        record = AuditTurnRecord(
            turn_id=str(uuid.uuid4()),
            call_id=call_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            logged_at=logged_at or datetime.now(timezone.utc),
            verdict=decision.verdict,
            question=question,
            answer_text=decision.answer_text,
            escalation_reason=decision.escalation_reason,
            confidence=decision.confidence,
            risk=decision.risk,
            trace_steps=_trace_to_skeleton(decision.trace),
            scope=decision.scope.value if decision.scope else None,
            needs_review=reason is not None,
            review_reason=reason,
        )
        updated: AuditCallRecord | None = None
        with self._lock:
            self._turns.append(record)
            call = self._calls.get(call_id)
            if call is not None:
                updated = call.model_copy(
                    update={
                        "turn_count": call.turn_count + 1,
                        "final_verdict": decision.verdict,
                        "escalated": call.escalated or decision.verdict is Verdict.ESCALATE,
                    }
                )
                self._calls[call_id] = updated
        self._persist_turn(record)
        if updated is not None:
            self._persist_call(updated)
        return record

    def log_call(
        self,
        *,
        call_id: str,
        patient_id: str,
        doctor_id: str,
        started_at: datetime | None = None,
    ) -> AuditCallRecord:
        """Open a call record (idempotent — returns existing if already logged)."""
        with self._lock:
            existing = self._calls.get(call_id)
            if existing is not None:
                return existing
            record = AuditCallRecord(
                call_id=call_id,
                patient_id=patient_id,
                doctor_id=doctor_id,
                started_at=started_at or datetime.now(timezone.utc),
            )
            self._calls[call_id] = record
        self._persist_call(record)
        return record

    def end_call(
        self,
        call_id: str,
        *,
        ended_at: datetime | None = None,
    ) -> AuditCallRecord | None:
        """Close a call record."""
        with self._lock:
            call = self._calls.get(call_id)
            if call is None:
                return None
            updated = call.model_copy(
                update={"ended_at": ended_at or datetime.now(timezone.utc)}
            )
            self._calls[call_id] = updated
        self._persist_call(updated)
        return updated

    def log_event(
        self,
        kind: AuditEventKind,
        *,
        patient_id: str | None = None,
        doctor_id: str | None = None,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
        logged_at: datetime | None = None,
    ) -> AuditEventRecord:
        """Record a non-turn audit event."""
        record = AuditEventRecord(
            event_id=str(uuid.uuid4()),
            kind=kind,
            logged_at=logged_at or datetime.now(timezone.utc),
            patient_id=patient_id,
            doctor_id=doctor_id,
            detail=detail,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._events.append(record)
        self._persist_event(record)
        return record

    def turns_for_call(self, call_id: str) -> list[AuditTurnRecord]:
        return [t for t in self.turns if t.call_id == call_id]

    def turns_for_patient(self, *, doctor_id: str, patient_id: str) -> list[AuditTurnRecord]:
        """One patient's turns under one doctor — always tenant-keyed (REVIEW-1).

        ``patient_id`` is only unique *within* a doctor, so a patient-scoped read
        keyed by ``patient_id`` alone would cross tenants (sev-0). There is no
        unscoped variant.
        """
        return [
            t for t in self.turns if t.doctor_id == doctor_id and t.patient_id == patient_id
        ]

    def turns_for_doctor(self, doctor_id: str) -> list[AuditTurnRecord]:
        """All logged turns for one doctor, newest first."""
        turns = [t for t in self.turns if t.doctor_id == doctor_id]
        return sorted(turns, key=lambda t: t.logged_at, reverse=True)

    def escalations_for_doctor(self, doctor_id: str) -> list[AuditTurnRecord]:
        """Doctor-scoped turns that terminated in ESCALATE, newest first."""
        return [t for t in self.turns_for_doctor(doctor_id) if t.verdict is Verdict.ESCALATE]

    def reviews_for_doctor(self, doctor_id: str) -> list[AuditTurnRecord]:
        """Doctor-scoped redirected turns flagged for review, newest first."""
        return [t for t in self.turns_for_doctor(doctor_id) if t.needs_review]

    def calls_for_doctor(self, doctor_id: str) -> list[AuditCallRecord]:
        with self._lock:
            calls = list(self._calls.values())
        return [c for c in calls if c.doctor_id == doctor_id]

    def get_call(self, call_id: str) -> AuditCallRecord | None:
        with self._lock:
            return self._calls.get(call_id)

    # --- escalation resolution (human-in-the-loop reply) ---------------------

    def resolve_escalation(
        self,
        *,
        turn_id: str,
        reply_text: str,
        resolved_by: str,
        resolved_at: datetime | None = None,
    ) -> AuditResolutionRecord | None:
        """Record the doctor's reply that closes an escalated turn.

        Returns ``None`` if the turn is unknown. The reply is keyed to the turn so
        the patient can be shown the answer to the exact question they asked.
        """
        with self._lock:
            turn = next((t for t in self._turns if t.turn_id == turn_id), None)
            if turn is None:
                return None
            record = AuditResolutionRecord(
                turn_id=turn_id,
                patient_id=turn.patient_id,
                doctor_id=turn.doctor_id,
                reply_text=reply_text,
                resolved_by=resolved_by,
                resolved_at=resolved_at or datetime.now(timezone.utc),
            )
            self._resolutions[turn_id] = record
        self._persist_resolution(record)
        return record

    def resolution_for(self, turn_id: str) -> AuditResolutionRecord | None:
        with self._lock:
            return self._resolutions.get(turn_id)

    def resolutions_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> list[AuditResolutionRecord]:
        """One patient's doctor replies under one doctor — tenant-keyed (REVIEW-1)."""
        with self._lock:
            resolutions = list(self._resolutions.values())
        return [r for r in resolutions if r.doctor_id == doctor_id and r.patient_id == patient_id]

    # --- human feedback (second online-evaluation channel) -------------------

    def rate_turn(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        turn_id: str,
        helpful: bool,
        comment: str | None = None,
        rated_at: datetime | None = None,
    ) -> AuditFeedbackRecord | None:
        """Record the patient's rating of their own turn (re-rating overwrites).

        Returns ``None`` unless the turn belongs to exactly ``(doctor_id,
        patient_id)`` — a cross-patient / cross-tenant turn id is
        indistinguishable from an unknown one (REVIEW-1). Raises
        :class:`NotRatableError` for a turn whose verdict is not ratable.
        """
        with self._lock:
            turn = next((t for t in self._turns if t.turn_id == turn_id), None)
            if turn is None or turn.doctor_id != doctor_id or turn.patient_id != patient_id:
                return None
            if turn.verdict not in RATABLE_VERDICTS:
                raise NotRatableError(turn.verdict.value)
            record = AuditFeedbackRecord(
                turn_id=turn_id,
                patient_id=turn.patient_id,
                doctor_id=turn.doctor_id,
                verdict=turn.verdict,
                helpful=helpful,
                comment=comment,
                rated_at=rated_at or datetime.now(timezone.utc),
            )
            self._feedback[turn_id] = record
        self._persist_optional("save_feedback", record)
        return record

    def review_turn(
        self,
        *,
        doctor_id: str,
        turn_id: str,
        correct: bool,
        reviewed_by: str,
        note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> AuditReviewRecord | None:
        """Record the doctor's correct / incorrect review of one of their turns.

        Any verdict may be reviewed (the verdict is recorded). Returns ``None``
        when the turn is unknown or belongs to another doctor (REVIEW-1).
        """
        with self._lock:
            turn = next((t for t in self._turns if t.turn_id == turn_id), None)
            if turn is None or turn.doctor_id != doctor_id:
                return None
            record = AuditReviewRecord(
                turn_id=turn_id,
                patient_id=turn.patient_id,
                doctor_id=turn.doctor_id,
                verdict=turn.verdict,
                correct=correct,
                note=note,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at or datetime.now(timezone.utc),
            )
            self._reviews[turn_id] = record
        self._persist_optional("save_review", record)
        return record

    def feedback_for(self, turn_id: str) -> AuditFeedbackRecord | None:
        with self._lock:
            return self._feedback.get(turn_id)

    def review_for(self, turn_id: str) -> AuditReviewRecord | None:
        with self._lock:
            return self._reviews.get(turn_id)

    def clear_patient(self, *, doctor_id: str, patient_id: str) -> int:
        """Remove this patient's turns + doctor replies entirely (UI 'clear history').

        Distinct from :meth:`redact_patient` (DPDP — keeps a nulled skeleton for
        compliance): this fully drops the records so the patient's portal thread is
        empty after a practice/demo run. Keyed by ``(doctor_id, patient_id)`` so a
        same-named patient under another doctor is never touched (REVIEW-1).
        Returns the number of turns removed.
        """

        def _mine(
            record: AuditTurnRecord
            | AuditResolutionRecord
            | AuditFeedbackRecord
            | AuditReviewRecord,
        ) -> bool:
            return record.doctor_id == doctor_id and record.patient_id == patient_id

        with self._lock:
            before = len(self._turns)
            self._turns = [t for t in self._turns if not _mine(t)]
            self._resolutions = {
                tid: r for tid, r in self._resolutions.items() if not _mine(r)
            }
            self._feedback = {tid: f for tid, f in self._feedback.items() if not _mine(f)}
            self._reviews = {tid: r for tid, r in self._reviews.items() if not _mine(r)}
            removed = before - len(self._turns)
        # Best-effort durable delete — keep the in-memory clear even if storage hiccups.
        if self._store is not None and hasattr(self._store, "delete_patient"):
            try:
                self._store.delete_patient(  # type: ignore[attr-defined]
                    doctor_id=doctor_id, patient_id=patient_id
                )
            except Exception:  # noqa: BLE001
                pass
        return removed

    def redact_patient(self, patient_id: str, *, doctor_id: str) -> int:
        """DPDP erasure — null clinical text, keep audit skeleton.

        Tenant-keyed: only this doctor's records for ``patient_id`` are redacted, so
        one doctor's erasure request never rewrites another tenant's audit trail.
        Returns the number of records redacted (turns + calls).
        """
        changed_turns: list[AuditTurnRecord] = []
        changed_calls: list[AuditCallRecord] = []
        with self._lock:
            redacted_turns: list[AuditTurnRecord] = []
            for turn in self._turns:
                if (
                    turn.doctor_id != doctor_id
                    or turn.patient_id != patient_id
                    or turn.redacted
                ):
                    redacted_turns.append(turn)
                    continue
                redacted = turn.model_copy(
                    update={
                        "question": None,
                        "answer_text": None,
                        "escalation_reason": None,
                        "review_reason": None,  # derived from the question text
                        "redacted": True,
                    }
                )
                redacted_turns.append(redacted)
                changed_turns.append(redacted)
            self._turns = redacted_turns

            for call_id, call in list(self._calls.items()):
                if (
                    call.doctor_id == doctor_id
                    and call.patient_id == patient_id
                    and not call.redacted
                ):
                    marked = call.model_copy(update={"redacted": True})
                    self._calls[call_id] = marked
                    changed_calls.append(marked)

            # Patient comments / doctor notes are free text: null them, keep the signal.
            changed_feedback: list[AuditFeedbackRecord] = []
            for tid, fb in list(self._feedback.items()):
                if fb.doctor_id == doctor_id and fb.patient_id == patient_id and fb.comment:
                    self._feedback[tid] = fb.model_copy(update={"comment": None})
                    changed_feedback.append(self._feedback[tid])
            changed_reviews: list[AuditReviewRecord] = []
            for tid, rv in list(self._reviews.items()):
                if rv.doctor_id == doctor_id and rv.patient_id == patient_id and rv.note:
                    self._reviews[tid] = rv.model_copy(update={"note": None})
                    changed_reviews.append(self._reviews[tid])

        # Overwrite the durable copies too (outside the lock — best-effort I/O).
        for redacted in changed_turns:
            self._persist_turn(redacted)
        for marked in changed_calls:
            self._persist_call(marked)
        for fb in changed_feedback:
            self._persist_optional("save_feedback", fb)
        for rv in changed_reviews:
            self._persist_optional("save_review", rv)
        count = len(changed_turns) + len(changed_calls)

        self.log_event(
            AuditEventKind.ERASURE,
            patient_id=patient_id,
            doctor_id=doctor_id,
            detail=f"redacted {count} audit record(s) — clinical text nulled",
        )
        return count


__all__ = [
    "AuditEventKind",
    "AuditTurnRecord",
    "AuditCallRecord",
    "AuditEventRecord",
    "AuditResolutionRecord",
    "AuditFeedbackRecord",
    "AuditReviewRecord",
    "AuditService",
    "NotRatableError",
    "RATABLE_VERDICTS",
    "review_reason",
]
