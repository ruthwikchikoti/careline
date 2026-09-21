"""``LocalMemoryProvider`` — the offline Layer-2 RAG twin (NG-2).

A dependency-free, in-process implementation of the
:class:`~careline.domain.ports.memory.MemoryProvider` port. It exists so the whole
suite (and the demo) can run the retrieval path with **no vector DB and no network**
— the keyless twin of a real embedding store, exactly as the heuristic reasoner is
the keyless twin of the LLM.

Retrieval is a deterministic **token-overlap** score (a tiny bag-of-words BM25-lite):
the more distinctive query terms a fact's text shares, the higher it ranks. It is
not meant to be clever — Layer 1 still disposes. Its job is only to *propose*
plausibly-relevant fact ids; the brain re-checks each against the valid slice and the
Verifier vetoes anything ungrounded. So an imperfect ranking can never make an unsafe
answer — at worst it surfaces a fact the valid-slice check then drops.

Isolation is structural: state is keyed by the ``(doctor_id, patient_id)`` pair and
every method requires both, so there is no path that reads across the namespace
boundary. ``forget`` drops a namespace wholesale for DPDP erasure.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

import re
from collections import Counter

from careline.domain.model.patient import ValidSlice
from careline.domain.ports.memory import MemoryHit, MemoryProvider

# Words too common to carry signal — dropped before scoring so "the medication"
# and "medication" rank a fact the same way.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "for",
        "and", "or", "in", "on", "at", "my", "i", "can", "do", "does", "what",
        "when", "should", "it", "this", "that", "with", "how", "am", "me",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class _Doc:
    """One indexed fact: its id, surfaceable text, kind, and token counts."""

    __slots__ = ("fact_id", "text", "kind", "counts")

    def __init__(self, fact_id, text, kind, counts):
        self.fact_id = fact_id
        self.text = text
        self.kind = kind
        self.counts = counts


class LocalMemoryProvider(MemoryProvider):
    """In-memory, offline keyword retriever — the keyless ``MemoryProvider`` twin."""

    def __init__(self) -> None:
        # namespace -> list of indexed docs. The namespace key embeds the tenant,
        # so there is no shared corpus a cross-patient query could reach.
        self._store: dict[tuple[str, str], list[_Doc]] = {}

    @staticmethod
    def _key(doctor_id: str, patient_id: str) -> tuple[str, str]:
        return (doctor_id, patient_id)

    async def index(self, *, doctor_id: str, patient_id: str, slice: ValidSlice) -> None:
        """Rebuild this patient's namespace from their approved, valid facts only."""
        docs = [
            _Doc(
                fact_id=f.id,
                text=f.summary,
                kind=f.kind,
                counts=Counter(_tokens(f.summary)),
            )
            for f in slice.facts
        ]
        self._store[self._key(doctor_id, patient_id)] = docs

    async def retrieve(
        self, *, doctor_id: str, patient_id: str, query: str, k: int = 5
    ) -> tuple[MemoryHit, ...]:
        """Return up to ``k`` token-overlap hits for this patient (highest score first)."""
        docs = self._store.get(self._key(doctor_id, patient_id), [])
        q = Counter(_tokens(query))
        if not q or not docs:
            return ()

        scored: list[tuple[float, _Doc]] = []
        for doc in docs:
            overlap = sum(min(q[t], doc.counts[t]) for t in q if t in doc.counts)
            if overlap > 0:
                scored.append((float(overlap), doc))

        # Highest score first; ties broken by fact_id for deterministic ordering.
        scored.sort(key=lambda s: (-s[0], s[1].fact_id))
        return tuple(
            MemoryHit(fact_id=doc.fact_id, text=doc.text, score=score, kind=doc.kind)
            for score, doc in scored[:k]
        )

    async def forget(self, *, doctor_id: str, patient_id: str) -> None:
        """Drop the whole namespace (DPDP erasure of Layer-2 for this patient)."""
        self._store.pop(self._key(doctor_id, patient_id), None)


__all__ = ["LocalMemoryProvider"]
