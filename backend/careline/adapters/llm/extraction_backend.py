"""OpenAI-backed Extraction agent adapter (#2 — real fix for #1).

The PRD calls extraction an "Extraction agent", but only the regex
:class:`~careline.services.extraction_service.HeuristicExtractor` existed — and
regex misses natural phrasing ("continue paracetamol", "follow a soft diet"),
yielding zero facts and a failed approval. This adapter is the real fix: an
OpenAI-backed :class:`~careline.domain.ports.extraction.Extractor` that structures
*any* phrasing, built on the same Responses API ``responses.parse`` path as
:mod:`careline.adapters.llm.openai_backend`.

Same safety contract as the reasoning adapters: the SDK is imported lazily, the LLM
is constrained to a strict schema (never free-text parsing), and any SDK error or a
``None`` parse raises :class:`ReasonerUnavailable` so the service persists nothing.
The heuristic extractor remains the keyless offline fallback; the factory
(:func:`careline.adapters.factory.build_extractor`) picks this adapter when an
OpenAI backend is configured.

Owner: Srujan (scope ``llm``). Default model: ``gpt-5.5``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from careline.adapters.llm import prompts
from careline.domain.ports.extraction import Extractor
from careline.domain.ports.reasoning import ReasonerUnavailable
from careline.services.extraction_service import ExtractedFactDTO, ExtractedRecord

DEFAULT_MODEL = "gpt-5.5"


class _ExtractionDTO(BaseModel):
    """The strict structured shape the Extraction LLM must emit.

    Only the facts — ``consultation_id`` and ``extracted_at`` are owned by the
    caller, never the model, so they are stamped on during the domain mapping.
    """

    model_config = ConfigDict(extra="forbid")

    facts: list[ExtractedFactDTO] = Field(default_factory=list)


class OpenAIExtractor(Extractor):
    """The Extraction agent backed by OpenAI structured outputs."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "high",  # accepted for a uniform factory signature; unused here
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = client  # injectable for tests; built lazily otherwise

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # lazy: optional dependency
        except ImportError as exc:  # pragma: no cover - only without the SDK
            raise ReasonerUnavailable("openai SDK is not installed") from exc
        self._client = OpenAI(api_key=self._api_key)
        return self._client

    def extract(
        self,
        *,
        transcript: str,
        consultation_id: str,
        now: datetime,
    ) -> ExtractedRecord:
        # An empty transcript has nothing to extract — return early, no API call.
        if not transcript or not transcript.strip():
            return ExtractedRecord(
                consultation_id=consultation_id, extracted_at=now, facts=()
            )

        client = self._ensure_client()
        try:
            response = client.responses.parse(
                model=self._model,
                instructions=prompts.EXTRACTOR_SYSTEM_PROMPT,
                input=prompts.build_extractor_user_message(transcript=transcript),
                text_format=_ExtractionDTO,
            )
        except ReasonerUnavailable:
            raise
        except Exception as exc:  # SDK / transport / validation — all fail closed
            raise ReasonerUnavailable(f"openai extraction failed: {exc}") from exc

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ReasonerUnavailable("openai returned no parseable extraction")
        return ExtractedRecord(
            consultation_id=consultation_id,
            extracted_at=now,
            facts=tuple(parsed.facts),
        )


__all__ = ["OpenAIExtractor", "DEFAULT_MODEL"]
