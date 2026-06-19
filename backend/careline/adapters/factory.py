"""The adapter factory: selects which reasoning backend the spine runs on (SR-6).

One place decides whether the Brain talks to the offline heuristic twins, Anthropic,
or OpenAI. Everything downstream depends only on the :class:`Reasoner` / :class:`Verifier`
ports, so swapping the backend is a config change with **zero domain impact** — the
whole point of the hexagonal boundary.

Two safety rules live here:

* **Keyless by default.** With no configuration the factory returns the heuristic
  twins, so the suite and M0 run offline with no API key.
* **Production guard.** The heuristic twins are a *stand-in*, never a production
  brain. Selecting them while ``environment`` is production raises — you cannot
  accidentally ship the offline stub to real patients.

Memory-provider selection (Layer-2 RAG vs Mongo) is the third leg of this factory;
it is wired in once Naga's ``MemoryProvider`` port (NG-3) lands. See
:func:`build_memory`.

Owner: Srujan (scope ``llm``) — a coordination-owned file (see COMMIT-ATTRIBUTION §5).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from careline.adapters.llm.anthropic_backend import AnthropicReasoner, AnthropicVerifier
from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier

_PROD_ENVS = frozenset({"prod", "production"})


class LLMBackend(str, Enum):
    """The reasoning backends the factory can wire in."""

    HEURISTIC = "heuristic"  # keyless offline twins (default)
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass(frozen=True)
class LLMConfig:
    """How to build the reasoning agents. Resolve from env with :meth:`from_env`."""

    backend: LLMBackend = LLMBackend.HEURISTIC
    environment: str = "dev"
    model: str | None = None  # None → the backend's own default model
    effort: str = "high"
    api_key: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LLMConfig":
        """Build a config from environment variables (all optional)."""
        env = os.environ if env is None else env
        raw = env.get("CARELINE_LLM_BACKEND", LLMBackend.HEURISTIC.value).lower()
        try:
            backend = LLMBackend(raw)
        except ValueError as exc:
            raise ValueError(f"unknown CARELINE_LLM_BACKEND: {raw!r}") from exc
        return cls(
            backend=backend,
            environment=env.get("CARELINE_ENV", "dev").lower(),
            model=env.get("CARELINE_LLM_MODEL") or None,
            effort=env.get("CARELINE_LLM_EFFORT", "high"),
            api_key=env.get("ANTHROPIC_API_KEY") or env.get("OPENAI_API_KEY") or None,
        )


def _guard(config: LLMConfig) -> None:
    """Refuse the offline stub in production — fail loud at wiring time."""
    if config.backend is LLMBackend.HEURISTIC and config.environment in _PROD_ENVS:
        raise RuntimeError(
            "refusing to use the offline heuristic reasoner in production; "
            "set CARELINE_LLM_BACKEND=anthropic|openai"
        )


def _kwargs(config: LLMConfig) -> dict:
    kwargs: dict = {"effort": config.effort, "api_key": config.api_key}
    if config.model:
        kwargs["model"] = config.model
    return kwargs


def build_reasoner(config: LLMConfig | None = None) -> Reasoner:
    """Build the :class:`Reasoner` for ``config`` (defaults: keyless heuristic)."""
    config = config or LLMConfig()
    _guard(config)
    if config.backend is LLMBackend.HEURISTIC:
        return HeuristicReasoner()
    if config.backend is LLMBackend.ANTHROPIC:
        return AnthropicReasoner(**_kwargs(config))
    if config.backend is LLMBackend.OPENAI:
        from careline.adapters.llm.openai_backend import OpenAIReasoner  # lazy: optional dep

        return OpenAIReasoner(**_kwargs(config))
    raise ReasonerUnavailable(f"no reasoner for backend {config.backend!r}")


def build_verifier(config: LLMConfig | None = None) -> Verifier:
    """Build the :class:`Verifier` for ``config`` (defaults: keyless heuristic)."""
    config = config or LLMConfig()
    _guard(config)
    if config.backend is LLMBackend.HEURISTIC:
        return HeuristicVerifier()
    if config.backend is LLMBackend.ANTHROPIC:
        return AnthropicVerifier(**_kwargs(config))
    if config.backend is LLMBackend.OPENAI:
        from careline.adapters.llm.openai_backend import OpenAIVerifier  # lazy: optional dep

        return OpenAIVerifier(**_kwargs(config))
    raise ReasonerUnavailable(f"no verifier for backend {config.backend!r}")


__all__ = ["LLMBackend", "LLMConfig", "build_reasoner", "build_verifier"]
