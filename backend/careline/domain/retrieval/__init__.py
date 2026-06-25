"""Retrieval-augmented grounding selection over the valid slice (RU-7).

A synchronous, dependency-free retrieval step that ranks a patient's already-valid
facts by relevance to the question and grounds the reasoner on the most relevant
subset. See :mod:`careline.domain.retrieval.ranker`.
"""

from careline.domain.retrieval.ranker import (
    DEFAULT_K,
    RankedFact,
    RetrievalResult,
    retrieval_detail,
    retrieve_relevant,
)

__all__ = [
    "DEFAULT_K",
    "RankedFact",
    "RetrievalResult",
    "retrieval_detail",
    "retrieve_relevant",
]
