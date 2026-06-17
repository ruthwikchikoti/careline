"""The §8.3 seed patient — one canonical record for offline runs (NG-2).

A single, deterministic :class:`Patient` used by the offline bake-off, the demo,
and the Layer-2 ``LocalMemoryProvider``. It is built to exercise the safety spine
without a database:

* a **current** medication and a **superseded** one (T1: discontinued-med recall),
* a **current** diet instruction and a **superseded** one (T2: stale guidance),
* a standing allergy and a scheduled follow-up,

all under one tenant (``dr-X``) for one patient (``patient-A``) — matching the ids
the bake-off uses, so the seed is the *single* source of canonical test data rather
than each test re-inventing facts.

The timestamps are fixed (no ``now()``) so the seed is reproducible: ``_SUPERSEDED``
falls before ``SEED_NOW``, so the superseded facts drop out of
``valid_slice(SEED_NOW)`` by construction — the "valid slice drops a superseded
fact" demonstration, seeded.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from careline.domain.model.fact import Allergy, FollowUp, Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.temporal import Validity

# Fixed reference instants — deterministic, no wall-clock.
SEED_DOCTOR_ID = "dr-X"
SEED_PATIENT_ID = "patient-A"

_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)             # when the record began
_SUPERSEDED = datetime(2026, 6, 1, tzinfo=timezone.utc)       # when old facts were retired
SEED_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)  # the "current" instant

_OPEN = Validity(effective_from=_PAST)
_RETIRED = Validity(effective_from=_PAST, superseded_at=_SUPERSEDED)


def seed_patient() -> Patient:
    """Return the canonical §8.3 seed patient (approved, time-stamped facts)."""
    return Patient(
        patient_id=SEED_PATIENT_ID,
        doctor_id=SEED_DOCTOR_ID,
        facts=(
            # Current medication.
            Medication(
                id="med-1",
                validity=_OPEN,
                summary="Paracetamol 500mg twice daily for pain.",
                name="Paracetamol",
                dose="500mg",
                frequency="twice daily",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
            # Discontinued antibiotic — superseded before SEED_NOW (T1).
            Medication(
                id="med-2",
                validity=_RETIRED,
                summary="Amoxicillin 250mg thrice daily (discontinued).",
                name="Amoxicillin",
                dose="250mg",
                frequency="thrice daily",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
            # Current diet instruction.
            Instruction(
                id="instr-1",
                validity=_OPEN,
                summary="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                text="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
            # Superseded diet instruction — replaced by instr-1 (T2).
            Instruction(
                id="instr-2",
                validity=_RETIRED,
                summary="Liquid diet only for 48 hours post-surgery (expired).",
                text="Liquid diet only for 48 hours post-surgery.",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
            # Standing allergy.
            Allergy(
                id="alg-1",
                validity=_OPEN,
                summary="Allergic to penicillin — causes rash.",
                substance="penicillin",
                reaction="rash",
                severity="moderate",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
            # Scheduled follow-up.
            FollowUp(
                id="fu-1",
                validity=_OPEN,
                summary="Follow-up review in 2 weeks.",
                scheduled_for=datetime(2026, 6, 29, tzinfo=timezone.utc),
                with_whom="Dr. X",
                approved_by=SEED_DOCTOR_ID,
                approved_at=_PAST,
            ),
        ),
    )


__all__ = [
    "seed_patient",
    "SEED_DOCTOR_ID",
    "SEED_PATIENT_ID",
    "SEED_NOW",
]
