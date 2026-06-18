"""Domain ↔ BSON mappers for the Layer-1 store (NG-4).

The longitudinal record is *domain* objects (frozen pydantic ``Fact`` subtypes with
a nested :class:`~careline.domain.model.temporal.Validity`). MongoDB wants flat BSON
documents. These pure functions translate between the two — and they are the *only*
place that knows the document shape, so the query builders and repositories never
hand-assemble BSON.

Two deliberate shape decisions, both in service of safety-by-query:

1. **Validity is flattened** into top-level ``effective_from`` / ``superseded_at``
   fields. The half-open valid-slice predicate (``effective_from <= now <
   superseded_at``) then becomes a plain indexed range query (NG-5) — a superseded
   fact is filtered out by the database, never read back and re-checked in Python.
2. **``doctor_id`` + ``patient_id`` lead every document.** Combined with the
   tenant-leading indexes, the scope filter is the prefix of every query, which is
   what makes cross-tenant reachability an absent code path, not a guard.

The **date↔datetime bridge**: BSON has no ``date`` type and drivers may hand back
*naive* datetimes. To keep the half-open comparison total and correct, every instant
is normalised to timezone-aware UTC on the way back out (:func:`_utc`), so a value
round-tripped through Mongo compares identically to the in-memory original.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from careline.domain.enums import FactKind
from careline.domain.model.fact import (
    Allergy,
    Diagnosis,
    Fact,
    FollowUp,
    Instruction,
    Medication,
    Observation,
)
from careline.domain.model.temporal import Validity

# Which concrete subtype to rebuild for each stored ``kind``.
_KIND_TO_CLASS: dict[FactKind, type[Fact]] = {
    FactKind.MEDICATION: Medication,
    FactKind.INSTRUCTION: Instruction,
    FactKind.DIAGNOSIS: Diagnosis,
    FactKind.OBSERVATION: Observation,
    FactKind.ALLERGY: Allergy,
    FactKind.FOLLOW_UP: FollowUp,
}

# Document keys that are structural (not fact fields) — excluded when rebuilding.
_RESERVED = frozenset({"_id", "doctor_id", "patient_id", "kind", "effective_from", "superseded_at"})


def _utc(value: Any) -> Any:
    """Normalise an instant to timezone-aware UTC (the date↔datetime bridge).

    A ``date`` (no time) is lifted to midnight UTC; a *naive* ``datetime`` (as some
    drivers return) is assumed UTC and stamped; an aware datetime is converted to
    UTC. Anything else is returned untouched.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return value


def fact_to_doc(fact: Fact, *, doctor_id: str, patient_id: str) -> dict[str, Any]:
    """Flatten a domain ``Fact`` into a tenant-scoped BSON document.

    ``validity`` is hoisted to top-level range columns and ``id`` becomes ``_id`` so
    a fact is upserted by its own identity.
    """
    data = fact.model_dump()
    validity = data.pop("validity")
    data.pop("kind", None)
    fact_id = data.pop("id")
    return {
        "_id": fact_id,
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "kind": fact.kind.value,
        "effective_from": validity["effective_from"],
        "superseded_at": validity["superseded_at"],
        **data,  # summary, approved_by/at, and the kind-specific fields
    }


def doc_to_fact(doc: dict[str, Any]) -> Fact:
    """Rebuild the correct ``Fact`` subtype from a stored document.

    The reverse of :func:`fact_to_doc`. Reconstructs :class:`Validity` from the flat
    columns and normalises every instant back to UTC so the rehydrated fact compares
    exactly like the one that was stored.
    """
    kind = FactKind(doc["kind"])
    cls = _KIND_TO_CLASS[kind]
    validity = Validity(
        effective_from=_utc(doc["effective_from"]),
        superseded_at=_utc(doc.get("superseded_at")),
    )
    fields = {k: v for k, v in doc.items() if k not in _RESERVED}
    # Normalise any datetime-valued fact fields (e.g. approved_at, scheduled_for).
    fields = {k: _utc(v) for k, v in fields.items()}
    return cls(id=doc["_id"], validity=validity, **fields)


__all__ = ["fact_to_doc", "doc_to_fact"]
