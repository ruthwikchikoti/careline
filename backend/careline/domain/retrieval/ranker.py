"""Retrieval-augmented grounding selection — sync, over the valid slice (RU-7).

The answer path is synchronous (the Brain and the gate chain run un-awaited from
the FastAPI routes), while the Layer-2 :class:`~careline.domain.ports.memory.MemoryProvider`
port is async (it can sit on Mongo or a vector store). To bring retrieval onto the
critical answer path *without* an async refactor — and without ever weakening the
safety invariant — this module ranks the patient's **already-valid** facts by
relevance to the question and selects the most relevant subset to ground the reasoner.

Why this is safe by construction:

- It ranks over the **valid slice** (Layer-1 source of truth: approved AND valid now),
  so every retrieved fact is, by definition, already re-validated. There is no path
  that surfaces a superseded, unapproved, or another patient's fact.
- For a small record (``total <= k``) it returns the **full** slice unchanged, so the
  reasoner sees exactly what it saw before — existing verdicts are byte-for-byte stable.
- For a rich record (``total > k``) it narrows to the top-``k`` most relevant facts. The
  citations the reasoner can produce are then a subset of the valid slice, so the
  confidence gate's citation-validity check (which runs against the *full* slice) still
  holds, and the Verifier still vetoes anything ungrounded.

The ranking uses the same deterministic **token-overlap** score as the offline
``LocalMemoryProvider`` (a tiny bag-of-words BM25-lite), so the on-path retriever and
the indexed Layer-2 provider speak the same language; the latter is the seam to scale
to an embedding/vector store behind the unchanged ``MemoryProvider`` port.

Owner: Ruthwik (scope ``graph``/integration).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from careline.domain.enums import FactKind
from careline.domain.model.patient import ValidSlice

#: Default number of facts to ground on. Generous enough that realistic records are
#: not narrowed (retrieval still *runs* and ranks); large records get a relevant top-k.
DEFAULT_K: int = 6

#: Fact kinds that retrieval may trim when narrowing a large record. Everything else —
#: medications, allergies, instructions, diagnoses — is **clinically actionable** and is
#: ALWAYS grounded, regardless of token-overlap score. This makes narrowing safety-aware:
#: a drug interaction, contraindication, or allergy can never be dropped from the reasoner's
#: view by a lexical-relevance miss. Only the lowest-risk kinds (observations, follow-ups)
#: are ever trimmed, and the Verifier still sees the *full* slice as an independent backstop.
_NARROWABLE_KINDS = frozenset({FactKind.OBSERVATION, FactKind.FOLLOW_UP})

# Words too common to carry signal — dropped before scoring (mirrors LocalMemoryProvider
# so the on-path retriever and the indexed Layer-2 provider rank identically).
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


@dataclass(frozen=True)
class RankedFact:
    """One valid fact and its relevance score for this question (higher = closer)."""

    fact_id: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    """The outcome of retrieval over the valid slice.

    ``grounding`` is what the reasoner/verifier should use as context; ``ranked`` is
    every valid fact with its relevance score (highest first) for the trace/UI.
    """

    grounding: ValidSlice
    ranked: tuple[RankedFact, ...]
    total: int
    kept: int
    narrowed: bool


def retrieve_relevant(
    *, question: str, valid_slice: ValidSlice, k: int = DEFAULT_K
) -> RetrievalResult:
    """Rank the valid slice by relevance to ``question`` and pick the grounding context.

    Deterministic and side-effect free. A small record passes through unchanged
    (``narrowed=False``). A large record is narrowed **safety-aware**: every clinically
    actionable fact (medication, allergy, instruction, diagnosis) is always kept; only the
    lowest-risk kinds (observations, follow-ups) are trimmed, and even those only by
    relevance to fill the budget. Every kept fact is a subset of ``valid_slice``.
    """
    facts = valid_slice.facts
    total = len(facts)

    q = Counter(_tokens(question))
    scored: dict[str, float] = {}
    for fact in facts:
        counts = Counter(_tokens(fact.summary))
        scored[fact.id] = float(sum(min(q[t], counts[t]) for t in q if t in counts))

    # Highest score first; ties broken by fact id for a deterministic, parity-stable order.
    order = sorted(facts, key=lambda f: (-scored[f.id], f.id))
    ranked = tuple(RankedFact(fact_id=f.id, score=scored[f.id]) for f in order)

    # Safety-aware selection: never drop a clinically-actionable fact. Only the narrowable
    # tail (observations, follow-ups) competes for the leftover budget by relevance.
    always_keep = [f for f in facts if f.kind not in _NARROWABLE_KINDS]
    tail = [f for f in facts if f.kind in _NARROWABLE_KINDS]

    if total <= k or not tail:
        # Small record, or nothing trimmable — ground on the full slice unchanged.
        return RetrievalResult(
            grounding=valid_slice, ranked=ranked, total=total, kept=total, narrowed=False
        )

    budget = max(0, k - len(always_keep))
    tail_by_relevance = sorted(tail, key=lambda f: (-scored[f.id], f.id))[:budget]
    keep_ids = {f.id for f in always_keep} | {f.id for f in tail_by_relevance}

    if len(keep_ids) == total:
        # Budget covered everything — no actual narrowing happened.
        return RetrievalResult(
            grounding=valid_slice, ranked=ranked, total=total, kept=total, narrowed=False
        )

    # Preserve the slice's original ordering among the kept facts for stable rendering.
    kept_facts = tuple(f for f in facts if f.id in keep_ids)
    grounding = ValidSlice(as_of=valid_slice.as_of, facts=kept_facts)
    return RetrievalResult(
        grounding=grounding,
        ranked=ranked,
        total=total,
        kept=len(kept_facts),
        narrowed=True,
    )


def retrieval_detail(result: RetrievalResult) -> str:
    """A short, human-readable trace line for the retrieval step."""
    kept_ids = ", ".join(f.id for f in result.grounding.facts) or "(none)"
    if result.narrowed:
        return (
            f"ranked {result.total} valid facts; grounding reasoner on {result.kept} "
            f"(all actionable facts kept; trimmed low-risk observations/follow-ups by "
            f"relevance) [{kept_ids}]"
        )
    return f"ranked {result.total} valid facts; grounding on all [{kept_ids}]"


__all__ = [
    "DEFAULT_K",
    "RankedFact",
    "RetrievalResult",
    "retrieval_detail",
    "retrieve_relevant",
]
