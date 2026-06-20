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
class InternalPrincipal:
    """Verified internal service caller (e.g. telephony bridge). Carries no tenant."""

    pass


__all__ = ["DoctorPrincipal", "InternalPrincipal"]
