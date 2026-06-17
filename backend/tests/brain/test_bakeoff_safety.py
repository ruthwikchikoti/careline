"""T1–T8 behavioural bake-off — the evaluation harness (VI-4).

Eight scenarios that prove the safety spine works offline/keyless.  Each test
builds a realistic ``Patient`` with time-stamped facts, simulates a proposal
from the Reasoner (via the heuristic twin's shape), runs the gate chain, and
asserts the safe verdict.

The scenarios:

T1  Discontinued-med recall        — superseded med → ESCALATE
T2  Superseded guidance            — expired instruction → ESCALATE
T3  Cross-condition conflict       — diabetic + post-op → ESCALATE
T4  Current vs historical          — only current facts in answer
T5  In-scope happy path            — clearly answerable → ANSWER
T6  Cross-patient isolation        — wrong patient → empty → ESCALATE
T7  Contradiction handling         — old + new guidance → ESCALATE
T8  Latency under load             — gate chain completes within budget

All run with **no API key, no database, no LLM** — pure domain objects.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import ReasoningTrace
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient, ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.model.temporal import Validity
from careline.domain.thresholds import Thresholds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SUPERSEDED = datetime(2026, 6, 1, tzinfo=timezone.utc)   # before _NOW
_FUTURE = datetime(2026, 12, 31, tzinfo=timezone.utc)


def _make_session() -> CallSession:
    return CallSession(
        call_id="call-001",
        patient_id="patient-A",
        doctor_id="dr-X",
        max_clarify_turns=2,
    )


def _seed_patient() -> Patient:
    """A realistic patient with current + superseded facts."""
    return Patient(
        patient_id="patient-A",
        doctor_id="dr-X",
        facts=(
            # Current medication
            Medication(
                id="med-1",
                kind=FactKind.MEDICATION,
                validity=Validity(effective_from=_PAST),
                summary="Paracetamol 500mg twice daily for pain.",
                name="Paracetamol",
                dose="500mg",
                frequency="twice daily",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
            # Discontinued medication (superseded before _NOW)
            Medication(
                id="med-2",
                kind=FactKind.MEDICATION,
                validity=Validity(effective_from=_PAST, superseded_at=_SUPERSEDED),
                summary="Amoxicillin 250mg thrice daily (discontinued).",
                name="Amoxicillin",
                dose="250mg",
                frequency="thrice daily",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
            # Current diet instruction
            Instruction(
                id="instr-1",
                kind=FactKind.INSTRUCTION,
                validity=Validity(effective_from=_PAST),
                summary="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                text="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
            # Superseded diet instruction (replaced by instr-1)
            Instruction(
                id="instr-2",
                kind=FactKind.INSTRUCTION,
                validity=Validity(effective_from=_PAST, superseded_at=_SUPERSEDED),
                summary="Liquid diet only for 48 hours post-surgery (expired).",
                text="Liquid diet only for 48 hours post-surgery.",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# T1: Discontinued-med recall
# ---------------------------------------------------------------------------


class TestT1DiscontinuedMed:
    """A superseded medication must not appear in the valid slice.

    If the question asks about the discontinued antibiotic, the valid slice
    won't contain it, so the proposal should be not-answerable → ESCALATE.
    """

    def test_superseded_med_absent_from_valid_slice(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        med_ids = [f.id for f in vs.facts if f.kind is FactKind.MEDICATION]
        assert "med-1" in med_ids, "current med should be present"
        assert "med-2" not in med_ids, "discontinued med must be absent"

    def test_question_about_discontinued_med_escalates(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        # Reasoner can't find the discontinued med → not answerable
        proposal = ClassifierProposal.not_answerable(
            ScopeCategory.IN_SCOPE,
            rationale="Amoxicillin not found in current valid facts",
        )
        ctx = GateContext(
            question="Should I still take Amoxicillin?",
            proposal=proposal,
            valid_slice=vs,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict in (Verdict.CLARIFY, Verdict.ESCALATE)


# ---------------------------------------------------------------------------
# T2: Superseded guidance
# ---------------------------------------------------------------------------


class TestT2SupersededGuidance:
    """An expired instruction must not be surfaced as current."""

    def test_superseded_instruction_absent(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        instr_ids = [f.id for f in vs.facts if f.kind is FactKind.INSTRUCTION]
        assert "instr-1" in instr_ids, "current instruction should be present"
        assert "instr-2" not in instr_ids, "superseded instruction must be absent"

    def test_question_about_expired_guidance_escalates(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        proposal = ClassifierProposal.not_answerable(
            ScopeCategory.IN_SCOPE,
            rationale="Liquid diet instruction is no longer valid",
        )
        ctx = GateContext(
            question="Am I still on the liquid-only diet?",
            proposal=proposal,
            valid_slice=vs,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict in (Verdict.CLARIFY, Verdict.ESCALATE)


# ---------------------------------------------------------------------------
# T3: Cross-condition conflict
# ---------------------------------------------------------------------------


class TestT3CrossCondition:
    """A question spanning diabetic + post-op conditions → ESCALATE."""

    def test_cross_condition_escalates(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        proposal = ClassifierProposal.not_answerable(
            ScopeCategory.CROSS_CONDITION,
            rationale="Question spans diabetes + surgery conditions",
            risk=0.95,
        )
        ctx = GateContext(
            question="Can I eat sweets post-surgery given my diabetes?",
            proposal=proposal,
            valid_slice=vs,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict is Verdict.ESCALATE


# ---------------------------------------------------------------------------
# T4: Current vs historical
# ---------------------------------------------------------------------------


class TestT4CurrentVsHistorical:
    """Only current facts should ground an answer, not historical ones."""

    def test_answer_cites_only_current_facts(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        # Proposal citing only the current instruction
        proposal = ClassifierProposal.answerable(
            "You should follow a soft diet and avoid spicy food.",
            citations=("instr-1",),
            confidence=0.9,
            risk=0.2,
            scope=ScopeCategory.IN_SCOPE,
        )
        verification = VerificationResult.affirm(confidence=0.9)
        ctx = GateContext(
            question="What diet should I follow now?",
            proposal=proposal,
            verification=verification,
            valid_slice=vs,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict is Verdict.ANSWER
        # Must cite the current instruction, never the superseded one
        assert "instr-1" in decision.citations
        assert "instr-2" not in decision.citations

    def test_history_is_accessible_but_not_current(self):
        patient = _seed_patient()
        history = patient.history(_NOW)
        hist_ids = [f.id for f in history]
        assert "med-2" in hist_ids or "instr-2" in hist_ids


# ---------------------------------------------------------------------------
# T5: In-scope happy path
# ---------------------------------------------------------------------------


class TestT5HappyPath:
    """A clearly answerable question with high confidence → ANSWER."""

    def test_in_scope_confident_verified_answers(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        proposal = ClassifierProposal.answerable(
            "Yes, you should continue taking Paracetamol 500mg twice daily.",
            citations=("med-1",),
            confidence=0.95,
            risk=0.1,
            scope=ScopeCategory.IN_SCOPE,
        )
        verification = VerificationResult.affirm(confidence=0.92)
        # Use a generous risk ceiling for happy-path
        thresholds = Thresholds(risk_ceiling=0.85)
        ctx = GateContext(
            question="Should I still take Paracetamol?",
            proposal=proposal,
            verification=verification,
            valid_slice=vs,
            thresholds=thresholds,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict is Verdict.ANSWER
        assert decision.answer_text is not None
        assert "Paracetamol" in decision.answer_text


# ---------------------------------------------------------------------------
# T6: Cross-patient isolation
# ---------------------------------------------------------------------------


class TestT6CrossPatientIsolation:
    """Another patient's data must never be reachable (sev-0)."""

    def test_wrong_patient_gets_empty_slice(self):
        patient = _seed_patient()
        # Simulate querying for a different patient — valid_slice is empty
        # because the Patient aggregate only holds its own facts.
        other_patient = Patient(
            patient_id="patient-B",
            doctor_id="dr-X",
            facts=(),  # no facts for this patient
        )
        vs = other_patient.valid_slice(_NOW)
        assert vs.is_empty, "cross-patient query must yield empty slice"

    def test_empty_slice_escalates(self):
        empty_slice = ValidSlice(as_of=_NOW, facts=())
        proposal = ClassifierProposal.not_answerable(
            ScopeCategory.IN_SCOPE,
            rationale="No facts found for this patient",
        )
        ctx = GateContext(
            question="What medication am I on?",
            proposal=proposal,
            valid_slice=empty_slice,
            now=_NOW,
            call_session=_make_session(),
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict is Verdict.ESCALATE


# ---------------------------------------------------------------------------
# T7: Contradiction handling
# ---------------------------------------------------------------------------


class TestT7ContradictionHandling:
    """Old + new guidance should not both surface; if contradiction → ESCALATE."""

    def test_verifier_veto_on_unsupported_claims_escalates(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        # Reasoner proposes an answer that mixes old + new guidance
        proposal = ClassifierProposal.answerable(
            "Follow a liquid diet as instructed.",
            citations=("instr-1",),  # cites current, but text contradicts
            confidence=0.7,
            risk=0.4,
        )
        # Verifier catches the contradiction — vetoes
        verification = VerificationResult.veto(
            unsupported_claims=("liquid diet is superseded by soft diet",),
            notes="Candidate contradicts current instruction",
        )
        # Exhaust clarify budget so contradiction forces ESCALATE
        session = _make_session()
        session.clarify_count = session.max_clarify_turns
        ctx = GateContext(
            question="What diet should I follow?",
            proposal=proposal,
            verification=verification,
            valid_slice=vs,
            now=_NOW,
            call_session=session,
        )
        decision = run_gate_chain(ctx)
        assert decision.verdict is Verdict.ESCALATE


# ---------------------------------------------------------------------------
# T8: Latency under load
# ---------------------------------------------------------------------------


class TestT8LatencyBudget:
    """The gate chain must complete within a reasonable time budget."""

    def test_gate_chain_completes_within_100ms(self):
        patient = _seed_patient()
        vs = patient.valid_slice(_NOW)
        proposal = ClassifierProposal.answerable(
            "Take Paracetamol 500mg twice daily.",
            citations=("med-1",),
            confidence=0.9,
            risk=0.1,
        )
        verification = VerificationResult.affirm(confidence=0.9)
        thresholds = Thresholds(risk_ceiling=0.85)
        ctx = GateContext(
            question="What's my painkiller dose?",
            proposal=proposal,
            verification=verification,
            valid_slice=vs,
            thresholds=thresholds,
            now=_NOW,
            call_session=_make_session(),
        )

        start = time.perf_counter()
        # Run the chain 100 times to stress-test
        for _ in range(100):
            # Need a fresh trace each iteration since it's mutable
            ctx_iter = GateContext(
                question=ctx.question,
                proposal=ctx.proposal,
                verification=ctx.verification,
                valid_slice=ctx.valid_slice,
                thresholds=ctx.thresholds,
                now=ctx.now,
                call_session=_make_session(),
                trace=ReasoningTrace(),
            )
            run_gate_chain(ctx_iter)
        elapsed = time.perf_counter() - start

        # 100 iterations should complete in well under 1 second
        assert elapsed < 1.0, (
            f"gate chain too slow: 100 iterations took {elapsed:.3f}s"
        )
