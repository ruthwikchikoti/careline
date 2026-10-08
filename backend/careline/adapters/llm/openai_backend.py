"""OpenAI-backed Reasoner and Verifier adapters (SR-7).

The second live backend, built on OpenAI's Responses API structured-output path
(``responses.parse`` with ``text_format``). Its only reason to exist is to *prove*
the hexagonal boundary: adding a whole new provider is a new adapter file with
**zero** change to the domain, the ports, or the Anthropic adapter. The factory
(SR-6) can swap Anthropic↔OpenAI by config alone.

The safety contract is identical to the Anthropic adapter: the OpenAI SDK is
imported lazily (it is an optional dependency), structured outputs only (never
free-text parsing, never ``temperature``/``top_p``), and any SDK error or a
``None`` parse raises :class:`ReasonerUnavailable` so the Brain fails closed.

Owner: Srujan (scope ``llm``). Default model: ``gpt-4o-mini`` (see ``DEFAULT_MODEL``).
"""

from __future__ import annotations

import time

from careline.adapters.llm import prompts
from careline.adapters.llm import usage as usage_recorder
from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier

# Budget-first default (budget cap ~$20): swap via CARELINE_LLM_MODEL when a
# stronger model is wanted; the price table + usage recorder keep the bill visible.
DEFAULT_MODEL = "gpt-4o-mini"


class _OpenAIBase:
    """Shared construction + lazy client for the OpenAI adapters."""

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

    def _parse(self, *, agent: str, instructions: str, user_message: str, text_format):
        client = self._ensure_client()
        response = None
        start = time.perf_counter()
        try:
            response = client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=user_message,
                text_format=text_format,
            )
        except ReasonerUnavailable:
            raise
        except Exception as exc:  # SDK / transport / validation — all fail closed
            raise ReasonerUnavailable(f"openai call failed: {exc}") from exc
        finally:
            # Observability never blocks the clinical call: record whatever usage
            # the provider returned; failed calls are counted, not averaged in.
            usage_recorder.record(
                agent=agent,
                model=self._model,
                usage=getattr(response, "usage", None),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                success=response is not None,
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ReasonerUnavailable("openai returned no parseable structured output")
        return parsed


class OpenAIReasoner(_OpenAIBase, Reasoner):
    """The Reasoner agent backed by OpenAI structured outputs."""

    def propose(self, *, question: str, context: ValidSlice) -> ClassifierProposal:
        dto: ProposalDTO = self._parse(
            agent="reasoner",
            instructions=prompts.REASONER_SYSTEM_PROMPT,
            user_message=prompts.build_reasoner_user_message(question=question, context=context),
            text_format=ProposalDTO,
        )
        return dto.to_domain()


class OpenAIVerifier(_OpenAIBase, Verifier):
    """The independent Verifier agent backed by OpenAI structured outputs."""

    def verify(
        self,
        *,
        question: str,
        proposal: ClassifierProposal,
        context: ValidSlice,
    ) -> VerificationResult:
        dto: VerificationDTO = self._parse(
            agent="verifier",
            instructions=prompts.VERIFIER_SYSTEM_PROMPT,
            user_message=prompts.build_verifier_user_message(
                question=question,
                candidate_answer=proposal.candidate_answer or "",
                citations=proposal.citations,
                context=context,
            ),
            text_format=VerificationDTO,
        )
        return dto.to_domain()


__all__ = ["OpenAIReasoner", "OpenAIVerifier", "DEFAULT_MODEL"]
