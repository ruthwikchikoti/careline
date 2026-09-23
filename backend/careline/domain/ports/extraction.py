"""The extraction port — the boundary the Extraction agent lives behind (NR-3).

Track A's offline Extraction agent turns a consultation transcript into a
structured :class:`~careline.services.extraction_service.ExtractedRecord`. Like
the reasoning ports, every implementation that cannot produce a trustworthy result
MUST raise :class:`~careline.domain.ports.reasoning.ReasonerUnavailable` rather
than return a guess or a partial — the service catches it and persists nothing.

Owner: Naresh (scope ``track-a``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class Extractor(ABC):
    """Extract structured clinical facts from a consultation transcript."""

    @abstractmethod
    def extract(
        self,
        *,
        transcript: str,
        consultation_id: str,
        now: datetime,
    ) -> ExtractedRecord:
        """Return a structured extraction for ``transcript``.

        Raises:
            ReasonerUnavailable: if a trustworthy extraction cannot be produced.
        """
        raise NotImplementedError


__all__ = ["Extractor"]
