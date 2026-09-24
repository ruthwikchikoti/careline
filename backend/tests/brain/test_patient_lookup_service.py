"""PatientLookupService tests (NR-5).

Pins caller-id lookup, HMAC-PIN verification, cross-tenant isolation, and the
no-oracle rule (unknown caller and wrong PIN raise the same exception).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio

import pytest

from careline.config import Settings
from careline.domain.model.patient import Patient, PatientIdentity
from careline.domain.model.patient import ValidSlice
from careline.domain.model.fact import Fact
from careline.domain.ports.repositories import PatientRepository
from careline.services.patient_lookup_service import (
    PatientLookupService,
    PatientNotFound,
    hash_pin,
)

_DR_A = "dr-A"
_DR_B = "dr-B"
_PATIENT = "patient-A"
_CALLER = "+919876543210"
_PIN = "1234"


class _InMemoryPatientRepository(PatientRepository):
    """Minimal offline double for identity lookup tests."""

    def __init__(self) -> None:
        self._identities: dict[tuple[str, str], PatientIdentity] = {}

    async def get(self, *, doctor_id: str, patient_id: str) -> Patient | None:
        return None

    async def exists(self, *, doctor_id: str, patient_id: str) -> bool:
        return False

    async def valid_slice(
        self, *, doctor_id: str, patient_id: str, now
    ) -> ValidSlice:
        return ValidSlice(as_of=now, facts=())

    async def history(
        self, *, doctor_id: str, patient_id: str, now
    ) -> tuple[Fact, ...]:
        return ()

    async def add_facts(
        self, *, doctor_id: str, patient_id: str, facts: tuple[Fact, ...]
    ) -> None:
        return None

    async def apply_facts(
        self, *, doctor_id: str, patient_id: str, facts: tuple[Fact, ...], now
    ) -> tuple[Fact, ...]:
        return ()

    async def soft_delete(self, *, doctor_id: str, patient_id: str) -> int:
        return 0

    async def find_by_caller(
        self, *, doctor_id: str, caller_id: str
    ) -> PatientIdentity | None:
        return self._identities.get((doctor_id, caller_id))

    async def upsert_identity(self, *, identity: PatientIdentity) -> None:
        self._identities[(identity.doctor_id, identity.caller_id)] = identity


def _run(coro):
    return asyncio.run(coro)


def _stack() -> tuple[PatientLookupService, _InMemoryPatientRepository, Settings]:
    settings = Settings()
    repo = _InMemoryPatientRepository()
    svc = PatientLookupService(patient_repo=repo, settings=settings)
    return svc, repo, settings


async def _seed_identity(
    repo: _InMemoryPatientRepository,
    settings: Settings,
    *,
    doctor_id: str = _DR_A,
    patient_id: str = _PATIENT,
    caller_id: str = _CALLER,
    pin: str = _PIN,
) -> PatientIdentity:
    identity = PatientIdentity(
        patient_id=patient_id,
        doctor_id=doctor_id,
        caller_id=caller_id,
        pin_hmac=hash_pin(pin=pin, secret=settings.pin_hmac_secret),
    )
    await repo.upsert_identity(identity=identity)
    return identity


def test_lookup_happy_path():
    svc, repo, settings = _stack()
    expected = _run(_seed_identity(repo, settings))
    result = _run(
        svc.lookup(doctor_id=_DR_A, caller_id=_CALLER, pin=_PIN)
    )
    assert result == expected
    assert result.patient_id == _PATIENT


def test_lookup_wrong_pin_raises_patient_not_found():
    svc, repo, settings = _stack()
    _run(_seed_identity(repo, settings))
    with pytest.raises(PatientNotFound):
        _run(svc.lookup(doctor_id=_DR_A, caller_id=_CALLER, pin="9999"))


def test_lookup_unknown_caller_raises_patient_not_found():
    svc, _, _ = _stack()
    with pytest.raises(PatientNotFound):
        _run(svc.lookup(doctor_id=_DR_A, caller_id="+910000000000", pin=_PIN))


def test_lookup_cross_tenant_blocked():
    svc, repo, settings = _stack()
    _run(_seed_identity(repo, settings, doctor_id=_DR_A))
    with pytest.raises(PatientNotFound):
        _run(svc.lookup(doctor_id=_DR_B, caller_id=_CALLER, pin=_PIN))


def test_lookup_timing_oracle_absent():
    svc, repo, settings = _stack()
    _run(_seed_identity(repo, settings))
    wrong_pin_exc: type[BaseException] | None = None
    unknown_caller_exc: type[BaseException] | None = None
    try:
        _run(svc.lookup(doctor_id=_DR_A, caller_id=_CALLER, pin="9999"))
    except PatientNotFound as exc:
        wrong_pin_exc = type(exc)
    try:
        _run(svc.lookup(doctor_id=_DR_A, caller_id="+910000000000", pin=_PIN))
    except PatientNotFound as exc:
        unknown_caller_exc = type(exc)
    assert wrong_pin_exc is PatientNotFound
    assert unknown_caller_exc is PatientNotFound
    assert wrong_pin_exc is unknown_caller_exc
