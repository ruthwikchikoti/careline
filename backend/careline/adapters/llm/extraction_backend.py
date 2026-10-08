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

Every call is recorded in :mod:`careline.adapters.llm.usage` as agent
``"extractor"`` (failed calls included), so extraction spend shows up in the
cost report next to the reasoner/verifier.

Owner: Srujan (scope ``llm``). Default model: ``gpt-4o-mini``.
"""

from __future__ import annotations

import time
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from careline.adapters.llm import prompts
from careline.adapters.llm import usage as usage_recorder
from careline.domain.ports.extraction import Extractor
from careline.domain.ports.reasoning import ReasonerUnavailable
from careline.services.extraction_service import ExtractedFactDTO, ExtractedRecord

# Budget-first default (budget cap ~$20) and present in the usage price table —
# the previous unpriced default made extraction spend invisible.
DEFAULT_MODEL = "gpt-4o-mini"


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
        response = None
        start = time.perf_counter()
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
        finally:
            usage_recorder.record(
                agent="extractor",
                model=self._model,
                usage=getattr(response, "usage", None),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                success=response is not None,
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ReasonerUnavailable("openai returned no parseable extraction")
        return ExtractedRecord(
            consultation_id=consultation_id,
            extracted_at=now,
            facts=tuple(parsed.facts),
        )


__all__ = ["OpenAIExtractor", "DEFAULT_MODEL"]
