"""The Layer-2 memory / retrieval (RAG) port (NG-3).

CareLine is two-layered. **Layer 1** (MongoDB) is the source of truth — every fact
carries half-open validity and a doctor approval stamp. **Layer 2** is a pluggable
*memory* that answers a softer question: "across this patient's whole history, what
text is *relevant* to what they just asked?" That is retrieval/RAG, and it lives
behind this single port so the offline keyword twin and a real vector store are
interchangeable.

The cardinal rule is **memory proposes, source-of-truth disposes**. A
:class:`MemoryHit` is only a *candidate* — relevance, not truth. A hit contributes
to a confident answer **only** if Layer 1 confirms the underlying fact is approved
and valid *now*. So a hit deliberately carries ``fact_id``: the brain re-checks that
id against the valid slice and drops the hit if it is superseded or unapproved. The
Verifier then refuses to ground an answer on anything not in the valid slice.

Isolation is **structural, not checked**: every method takes a *required
keyword-only* ``doctor_id`` and ``patient_id``. There is no method that retrieves
across patients — the namespace is the (doctor, patient) pair, so a cross-patient
read is not a guard you can forget, it is an absent code path (a cross-patient leak
is sev-0).

Owner: Naga (scope ``data``). The offline ``LocalMemoryProvider`` and the
``MongoMemoryProvider`` both implement this ABC; Naresh's approval pipeline indexes
through it and the brain retrieves through it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import FactKind
from careline.domain.model.patient import ValidSlice


class MemoryHit(BaseModel):
    """A single Layer-2 retrieval candidate — *relevant*, not yet *true*.

    ``fact_id`` ties the hit back to the Layer-1 fact it came from so the brain can
    confirm it is still in the valid slice before any answer leans on it. ``score``
    is the provider's relevance score (higher = more relevant); it is **not** a
    confidence in the fact's truth — that is Layer 1's call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    text: str
    score: float = Field(ge=0.0)
    kind: FactKind | None = None


class MemoryProvider(ABC):
    """Layer-2 retrieval over one patient's longitudinal history.

    Implementations namespace strictly by the required ``doctor_id`` + ``patient_id``
    keyword arguments — there is no cross-patient retrieval path by construction.
    """

    @abstractmethod
    async def index(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        slice: ValidSlice,
    ) -> None:
        """(Re)build this patient's retrieval namespace from their approved facts.

        Called after the doctor approves facts (Naresh's pipeline). Only the facts
        in ``slice`` — approved and valid — are indexed, so memory never surfaces
        text the doctor has not signed off on.
        """
        raise NotImplementedError

    @abstractmethod
    async def retrieve(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        query: str,
        k: int = 5,
    ) -> tuple[MemoryHit, ...]:
        """Return up to ``k`` hits relevant to ``query`` for this patient only.

        An empty tuple is a valid, safe result (nothing relevant found) — never an
        error and never a fallback to another patient's namespace.
        """
        raise NotImplementedError

    @abstractmethod
    async def forget(self, *, doctor_id: str, patient_id: str) -> None:
        """Drop this patient's entire namespace (DPDP right-to-erasure on Layer 2)."""
        raise NotImplementedError


__all__ = ["MemoryHit", "MemoryProvider"]
