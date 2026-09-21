"""Motor client + tenant-leading indexes for the Layer-1 store (NG-4).

This module owns two things and nothing else: how to *connect* to MongoDB, and what
*indexes* the source-of-truth collections require. Both are isolated here so the
repositories depend on a ready ``database`` handle, never on connection or DDL
concerns — and so the suite can hand them a mock database instead.

Motor is imported **lazily** inside :func:`create_client`: the package imports and
the whole offline suite run with no ``motor``/``pymongo`` installed (same pattern as
the LLM adapters). The client is created with ``tz_aware=True`` so datetimes come
back timezone-aware — the read side of the date↔datetime bridge.

**Tenant-leading indexes** are the performance complement to structural isolation:
every index begins with ``doctor_id`` (then ``patient_id``), so every scoped query
is an index *prefix* hit and there is no efficient way to scan across tenants — the
storage layout itself nudges toward one-tenant-at-a-time access.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from typing import Any

# Collection names — the single source of these strings.
FACTS = "facts"
CONSULTATIONS = "consultations"
AUDIT = "audit"
DOCTORS = "doctors"
MEMORY = "memory"


def create_client(uri: str, **kwargs: Any) -> Any:
    """Create a Motor client (lazy import; fail-closed if the driver is absent).

    ``tz_aware=True`` is forced so the date↔datetime bridge holds on read.
    """
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:  # pragma: no cover - exercised only without motor
        raise RuntimeError(
            "motor is required for the live Mongo path; install the data extras"
        ) from exc
    kwargs.setdefault("tz_aware", True)
    return AsyncIOMotorClient(uri, **kwargs)


async def ensure_indexes(database: Any) -> None:
    """Create the tenant-leading indexes the Layer-1 queries rely on (idempotent).

    Every index leads with ``doctor_id`` so the scope filter is always an index
    prefix; the ``facts`` indexes additionally cover the valid-slice range columns
    (``effective_from`` / ``superseded_at``) and ``kind`` for kind-filtered slices.
    """
    facts = database[FACTS]
    await facts.create_index([("doctor_id", 1), ("patient_id", 1), ("effective_from", 1)])
    await facts.create_index([("doctor_id", 1), ("patient_id", 1), ("superseded_at", 1)])
    await facts.create_index([("doctor_id", 1), ("patient_id", 1), ("kind", 1)])

    consultations = database[CONSULTATIONS]
    await consultations.create_index([("doctor_id", 1), ("patient_id", 1)])

    audit = database[AUDIT]
    await audit.create_index([("doctor_id", 1), ("patient_id", 1), ("ts", 1)])

    memory = database[MEMORY]
    await memory.create_index([("doctor_id", 1), ("patient_id", 1)])


__all__ = [
    "create_client",
    "ensure_indexes",
    "FACTS",
    "CONSULTATIONS",
    "AUDIT",
    "DOCTORS",
    "MEMORY",
]
