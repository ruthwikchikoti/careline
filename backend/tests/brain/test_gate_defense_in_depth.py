"""Defense-in-depth for the scope gate — sev-0 regression battery.

Probes found that everyday emergency phrasings the regex rail
misses were classified ``OUT_OF_SCOPE`` by the offline reasoner and then
politely redirected to CLARIFY by the scope gate — a missed emergency that
never reaches the doctor. The scope label is a *classifier output*; it can be
wrong. These tests pin the chain's independence from its callers: before any
redirect, the gate itself must re-run the deterministic rails on the raw
question and fail closed on an empty valid slice.

Gates only downgrade: ESCALATE here preempts a would-be CLARIFY, never the
reverse — consistent with the chain's structural guarantee.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.model.fact import Medication
from careline.domain.model.patient import Patient
from careline.domain.model.proposal import ClassifierProposal
from careline.domain.model.temporal import Validity

_NOW = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _patient(*, empty: bool = False) -> Patient:
    facts = () if empty else (
        Medication(
            id="med-1",
            kind=FactKind.MEDICATION,
            validity=Validity(effective_from=_PAST),
            summary="Paracetamol 500mg twice daily.",
            name="Paracetamol",
            dose="500mg",
            frequency="twice daily",
            approved_by="dr-X",
            approved_at=_PAST,
        ),
    )
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=facts)


def _run(question: str, *, empty: bool = False) -> Verdict:
    """Run the gate chain with a mislabelled OUT_OF_SCOPE proposal.

    This is the adversarial frame: a caller (or reasoner) got the scope wrong;
    the chain alone must still be safe.
    """
    ctx = GateContext(
        question=question,
        proposal=ClassifierProposal.not_answerable(
            ScopeCategory.OUT_OF_SCOPE, rationale="question does not match any approved fact"
        ),
        verification=None,
        valid_slice=_patient(empty=empty).valid_slice(_NOW),
        now=_NOW,
    )
    return run_gate_chain(ctx).verdict


# (id, question, empty_slice, expected_verdict)
_CASES = [
    # Emergency phrase in the raw question — the scope label says redirect,
    # the verdict must say ESCALATE.
    (
        "red_flag_phrase_mislabeled_out_of_scope",
        "I have severe chest pain right now",
        False,
        Verdict.ESCALATE,
    ),
    # NOTE: paraphrased emergencies the regex vocabulary misses (e.g. "I want
    # to end my life") belong to the rail itself — they are pinned in
    # test_red_flag_semantic.py once the semantic detector policy lands.
    # Empty valid slice is the documented fail-closed escalate (gate 4's rule);
    # a mislabelled scope must not be able to shadow it into a redirect.
    (
        "empty_slice_mislabeled_out_of_scope",
        "What medication am I supposed to take?",
        True,
        Verdict.ESCALATE,
    ),
    # A cross-condition span the caller's tripwire missed.
    (
        "cross_condition_mislabeled_out_of_scope",
        "Can I take metformin together with my blood pressure tablets?",
        False,
        Verdict.ESCALATE,
    ),
    # Guard: a genuinely non-clinical question with a real valid slice is still
    # redirected (CLARIFY), not escalated — commit 7c8acf3's intent stands.
    (
        "benign_out_of_scope_still_redirects",
        "Who won the cricket match last night?",
        False,
        Verdict.CLARIFY,
    ),
]


@pytest.mark.parametrize(
    "name,question,empty,expected", _CASES, ids=[c[0] for c in _CASES]
)
def test_scope_gate_is_safe_independent_of_scope_label(name, question, empty, expected):
    verdict = _run(question, empty=empty)
    assert verdict is expected, (
        f"{name}: scope label OUT_OF_SCOPE produced {verdict.value}, expected {expected.value}"
    )
