"""Authenticated principal types — the only trusted source of doctor_id (NR-5).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoctorPrincipal:
    """A verified doctor session — ``doctor_id`` is never taken from the request body."""

    doctor_id: str


@dataclass(frozen=True)
class PatientPrincipal:
    """A verified patient session (patient portal — patient-ID/PIN login).

    Carries both the ``patient_id`` and the ``doctor_id`` it belongs to, so every
    patient-scoped read stays tenant- *and* patient-scoped: a patient can only ever
    reach their own record under their own doctor.
    """

    patient_id: str
    doctor_id: str


@dataclass(frozen=True)
class InternalPrincipal:
    """Verified internal service caller (e.g. telephony bridge). Carries no tenant."""

    pass


__all__ = ["DoctorPrincipal", "PatientPrincipal", "InternalPrincipal"]
