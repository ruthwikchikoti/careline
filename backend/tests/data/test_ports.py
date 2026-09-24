"""Port contract tests (NG-3).

The ports are abstract on purpose — you cannot instantiate a half-implemented
repository or memory provider, and every method is tenant-scoped by a required
keyword-only ``doctor_id``. These tests pin those two structural guarantees.
"""

import inspect

import pytest

from careline.domain.ports.memory import MemoryHit, MemoryProvider
from careline.domain.ports.repositories import (
    AuditRepository,
    ConsultationRepository,
    DoctorRepository,
    PatientRepository,
)

ALL_PORTS = [
    MemoryProvider,
    PatientRepository,
    ConsultationRepository,
    AuditRepository,
    DoctorRepository,
]


@pytest.mark.parametrize("port", ALL_PORTS)
def test_ports_are_abstract(port):
    with pytest.raises(TypeError):
        port()  # cannot instantiate an ABC with abstract methods


# ``save`` is scoped by the aggregate it receives (which carries ``doctor_id``
# itself), so it does not — and must not — take a separate doctor_id keyword.
_AGGREGATE_SCOPED = {"save", "upsert_identity"}


@pytest.mark.parametrize("port", ALL_PORTS)
def test_doctor_id_is_a_required_keyword_only_arg(port):
    """Every tenant-scoped port method takes ``doctor_id`` keyword-only — structural
    isolation cannot be bypassed by positional misuse, and there is no query method
    that omits the tenant scope."""
    for name, method in inspect.getmembers(port, predicate=inspect.isfunction):
        if name.startswith("_") or name in _AGGREGATE_SCOPED:
            continue
        params = inspect.signature(method).parameters
        assert "doctor_id" in params, f"{port.__name__}.{name} must take doctor_id"
        assert (
            params["doctor_id"].kind is inspect.Parameter.KEYWORD_ONLY
        ), f"{port.__name__}.{name} doctor_id must be keyword-only"


def test_memory_hit_rejects_negative_score():
    with pytest.raises(Exception):
        MemoryHit(fact_id="f1", text="x", score=-1.0)


def test_memory_hit_is_frozen():
    hit = MemoryHit(fact_id="f1", text="x", score=1.0)
    with pytest.raises(Exception):
        hit.score = 2.0  # type: ignore[misc]
