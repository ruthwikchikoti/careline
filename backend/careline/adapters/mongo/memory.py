"""``MongoMemoryProvider`` — the persisted Layer-2 RAG store (NG-6).

The Mongo-backed twin of the offline ``LocalMemoryProvider``: same
:class:`~careline.domain.ports.memory.MemoryProvider` contract, but the per-patient
namespace lives in the ``memory`` collection instead of a process dict. ``index``
writes one document per approved fact (its surfaceable text + pre-computed tokens);
``retrieve`` reads back *only this patient's* namespace and scores token overlap in
Python; ``forget`` deletes the namespace for DPDP erasure.

Why score in Python rather than a ``$text`` index: it keeps retrieval identical
between the offline and Mongo paths, works under the in-memory test double, and —
crucially — memory only *proposes*. The ranking never decides truth; the brain
re-checks each hit's ``fact_id`` against the Layer-1 valid slice, so a coarse score
can surface a candidate but can never make an unsafe answer.

Isolation is structural: every document carries ``doctor_id`` + ``patient_id`` and
every query is built with :func:`~careline.adapters.mongo.filters.scoped_filter`, so
no read crosses the namespace boundary.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from careline.adapters.memory.local import _tokens  # shared offline tokenizer
from careline.adapters.mongo.client import MEMORY
from careline.adapters.mongo.filters import scoped_filter
from careline.domain.enums import FactKind
from careline.domain.model.patient import ValidSlice
from careline.domain.ports.memory import MemoryHit, MemoryProvider


class MongoMemoryProvider(MemoryProvider):
    """Persisted per-patient keyword retrieval over the ``memory`` collection."""

    def __init__(self, database: Any) -> None:
        self._col = database[MEMORY]

    async def index(
        self, *, doctor_id: str, patient_id: str, slice: ValidSlice
    ) -> None:
        """Rebuild this patient's namespace from their approved, valid facts."""
        await self.forget(doctor_id=doctor_id, patient_id=patient_id)
        docs = [
            {
                "doctor_id": doctor_id,
                "patient_id": patient_id,
                "fact_id": f.id,
                "text": f.summary,
                "kind": f.kind.value,
                "tokens": _tokens(f.summary),
            }
            for f in slice.facts
        ]
        if docs:
            await self._col.insert_many(docs)

    async def retrieve(
        self, *, doctor_id: str, patient_id: str, query: str, k: int = 5
    ) -> tuple[MemoryHit, ...]:
        """Return up to ``k`` token-overlap hits for this patient only."""
        q = Counter(_tokens(query))
        if not q:
            return ()
        docs = await self._col.find(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        ).to_list(length=None)

        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in docs:
            counts = Counter(doc.get("tokens", []))
            overlap = sum(min(q[t], counts[t]) for t in q if t in counts)
            if overlap > 0:
                scored.append((float(overlap), doc))

        scored.sort(key=lambda s: (-s[0], s[1]["fact_id"]))
        return tuple(
            MemoryHit(
                fact_id=doc["fact_id"],
                text=doc["text"],
                score=score,
                kind=FactKind(doc["kind"]) if doc.get("kind") else None,
            )
            for score, doc in scored[:k]
        )

    async def forget(self, *, doctor_id: str, patient_id: str) -> None:
        """Delete this patient's whole namespace (DPDP erasure on Layer 2)."""
        await self._col.delete_many(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        )


__all__ = ["MongoMemoryProvider"]
