"""Anthropic-backed Reasoner and Verifier adapters (SR-5).

The live LLM implementations of the reasoning ports, built on the Anthropic SDK's
structured-output path (`messages.parse`). Each agent is constrained to emit one of
the strict DTOs from :mod:`careline.adapters.llm.schemas`; the validated object is
mapped into the domain handoff. The Anthropic SDK is imported **lazily** so the
offline suite never needs it installed.

Design constraints (the safety contract on the LLM path):

* **Fail closed.** Any SDK/transport error, a missing SDK, or a ``None``/refused
  parse raises :class:`ReasonerUnavailable` — never a guess. The Brain escalates.
* **Structured outputs only.** We pass the DTO as ``output_format`` and read
  ``parsed_output``; we never parse free text.
* **Adaptive thinking + effort, never sampling knobs.** We set
  ``thinking={"type": "adaptive"}`` and ``output_config={"effort": ...}`` and
  *never* ``temperature`` / ``top_p`` / ``budget_tokens`` (removed on current
  models, and a determinism/safety liability regardless).
* **Cache-friendly.** The frozen system prompt is sent as a cached block; only the
  per-turn question varies.

Owner: Srujan (scope ``llm``). Default model: ``claude-opus-4-8``.
"""

from __future__ import annotations

from careline.adapters.llm import prompts
from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 2048


def _cached_system(prompt: str) -> list[dict]:
    """The frozen system prompt as a single cache-anchored text block."""
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]


class _AnthropicBase:
    """Shared construction + lazy client for the Anthropic adapters."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._api_key = api_key
        self._client = client  # injectable for tests; built lazily otherwise

    def _ensure_client(self):
        """Return an Anthropic client, importing/constructing lazily.

        A missing SDK is treated as the dependency being unavailable — fail closed
        rather than letting an ImportError escape uncaught.
        """
        if self._client is not None:
            return self._client
        try:
            import anthropic  # lazy: keeps the offline suite keyless and dep-free
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise ReasonerUnavailable("anthropic SDK is not installed") from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _parse(self, *, system: list[dict], user_message: str, output_format):
        """Run one structured-output completion, failing closed on any problem."""
        client = self._ensure_client()
        try:
            response = client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self._effort},
                system=system,
                output_format=output_format,
                messages=[{"role": "user", "content": user_message}],
            )
        except ReasonerUnavailable:
            raise
        except Exception as exc:  # SDK / transport / validation — all fail closed
            raise ReasonerUnavailable(f"anthropic call failed: {exc}") from exc

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            # A refusal or a response that didn't validate against the schema.
            raise ReasonerUnavailable("anthropic returned no parseable structured output")
        return parsed


class AnthropicReasoner(_AnthropicBase, Reasoner):
    """The Reasoner agent backed by Anthropic structured outputs."""

    def propose(self, *, question: str, context: ValidSlice) -> ClassifierProposal:
        dto: ProposalDTO = self._parse(
            system=_cached_system(prompts.REASONER_SYSTEM_PROMPT),
            user_message=prompts.build_reasoner_user_message(question=question, context=context),
            output_format=ProposalDTO,
        )
        return dto.to_domain()


class AnthropicVerifier(_AnthropicBase, Verifier):
    """The independent Verifier agent backed by Anthropic structured outputs."""

    def verify(
        self,
        *,
        question: str,
        proposal: ClassifierProposal,
        context: ValidSlice,
    ) -> VerificationResult:
        dto: VerificationDTO = self._parse(
            system=_cached_system(prompts.VERIFIER_SYSTEM_PROMPT),
            user_message=prompts.build_verifier_user_message(
                question=question,
                candidate_answer=proposal.candidate_answer or "",
                citations=proposal.citations,
                context=context,
            ),
            output_format=VerificationDTO,
        )
        return dto.to_domain()


__all__ = ["AnthropicReasoner", "AnthropicVerifier", "DEFAULT_MODEL"]
