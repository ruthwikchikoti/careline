"""LLM-as-judge — faithfulness of an ANSWER against ONLY its cited facts.

The quality half of the LLM slice and of online evaluation. A judge reads the
exact answer the patient heard plus the text of the facts that answer cites —
nothing else, so it can never "rescue" an answer with outside knowledge — and
returns a structured verdict ``{faithful, score, unsupported_claims}``.

Two implementations, one contract:

* :class:`OpenAIJudge` — structured output via the Responses API
  (``responses.parse`` + :class:`JudgeDTO`), instructions loaded from the
  versioned, hash-checked artifact ``prompts/judge/v1.md``. Usage is recorded
  as agent ``"judge"`` so judge spend is visible separately in the cost report.
* :class:`KeylessJudge` — a deterministic token-overlap groundedness check,
  so online evaluation and CI work with no key and no network. Weaker than a
  model judge (it cannot see paraphrase), but it is conservative: a claim
  whose content tokens — numbers especially — are not in the cited facts is
  unsupported.

Fail closed: no cited facts or an empty answer is never faithful, and a
provider error raises :class:`JudgeUnavailable` — callers count it as an
error / unfaithful, never as a pass.

Owner: Srujan (scope ``llm``).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from careline.adapters.llm import prompt_registry
from careline.adapters.llm import usage as usage_recorder

DEFAULT_JUDGE_MODEL = "gpt-4o-mini"
KEYLESS_JUDGE_STAMP = "judge-keyless@v1"

# A claim counts as supported by the keyless twin when at least this share of
# its content tokens appears in the cited facts AND every number in it does.
_KEYLESS_SUPPORT = 0.75


class JudgeUnavailable(RuntimeError):
    """The judge could not produce a verdict (SDK/transport/parse failure)."""


class JudgeDTO(BaseModel):
    """The strict JSON the judge LLM must emit."""

    model_config = ConfigDict(extra="forbid")

    faithful: bool = Field(description="True only if no clinical claim is unsupported.")
    score: float = Field(ge=0.0, le=1.0, description="Fraction of claims supported.")
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Unsupported claims; empty when faithful."
    )


@dataclass(frozen=True)
class JudgeVerdict:
    faithful: bool
    score: float
    unsupported_claims: tuple[str, ...]
    judge: str  # stamp of the judge that produced it


def load_judge_prompt() -> prompt_registry.Artifact:
    """The versioned judge prompt, hash-checked against the manifest.

    ``prompt_registry.load_prompt`` whitelists the three spine prompts; the
    judge is registered in the same manifest and goes through the same
    tamper-checked loader (a hash mismatch raises ``RegistryError``).
    """
    return prompt_registry._load("prompts", "judge")


def build_judge_user_message(*, answer: str, facts: Sequence[tuple[str, str]]) -> str:
    """ANSWER + only the cited facts' text, id-tagged."""
    block = "\n".join(f"[{fid}] {text}" for fid, text in facts) or "(no cited facts)"
    return f"ANSWER:\n{answer}\n\nCITED FACTS:\n{block}"


def _not_faithful(stamp: str, reason: str) -> JudgeVerdict:
    return JudgeVerdict(faithful=False, score=0.0, unsupported_claims=(reason,), judge=stamp)


# ---------------------------------------------------------------------------
# Keyless twin
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[a-z]+")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
# Function words + the conversational wrapping answers use ("Your doctor advised: ...").
_STOP = frozenset(
    """
    a an the and or but if then of in on at to for with from by as is are was were be
    been am do does did have has had not no this that these those it its you your yours
    i me my we our they them their he she his her can could will would should may might
    must please also just so very per each any all some what when where which who how
    doctor doctors dr advised advise advice says said record approved according note
    noted recommended recommends instructions instruction plan care team call unsure
    """.split()
)


def _content_tokens(text: str) -> list[str]:
    return [
        t
        for t in _TOKEN_RE.findall((text or "").lower())
        if _NUMBER_RE.match(t) or (t not in _STOP and len(t) > 1)
    ]


class KeylessJudge:
    """Deterministic token-overlap groundedness check (no key, no network)."""

    stamp = KEYLESS_JUDGE_STAMP

    def judge(self, *, answer: str, facts: Sequence[tuple[str, str]]) -> JudgeVerdict:
        if not facts:
            return _not_faithful(self.stamp, "no cited facts to ground on")
        if not answer or not answer.strip():
            return _not_faithful(self.stamp, "empty answer")
        evidence = set()
        for _fid, text in facts:
            evidence.update(_content_tokens(text))
        claims = [c.strip() for c in _SENTENCE_RE.split(answer) if c.strip()]
        scored, unsupported = [], []
        for claim in claims:
            tokens = _content_tokens(claim)
            if not tokens:
                continue  # pure wrapping, no clinical content
            covered = sum(t in evidence for t in tokens) / len(tokens)
            numbers_ok = all(t in evidence for t in tokens if _NUMBER_RE.match(t))
            supported = covered >= _KEYLESS_SUPPORT and numbers_ok
            scored.append(covered if numbers_ok else min(covered, 0.5))
            if not supported:
                unsupported.append(claim)
        if not scored:
            return _not_faithful(self.stamp, "answer has no checkable clinical content")
        score = round(sum(scored) / len(scored), 4)
        return JudgeVerdict(
            faithful=not unsupported,
            score=score,
            unsupported_claims=tuple(unsupported),
            judge=self.stamp,
        )


# ---------------------------------------------------------------------------
# OpenAI judge
# ---------------------------------------------------------------------------


class OpenAIJudge:
    """LLM-as-judge on OpenAI structured outputs (prompt ``judge@v1``)."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_JUDGE_MODEL,
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = client  # injectable for tests; built lazily otherwise
        self._prompt = load_judge_prompt()  # fail closed at construction on tamper

    @property
    def stamp(self) -> str:
        return f"{self._prompt.stamp}:{self._model}"

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # lazy: optional dependency
        except ImportError as exc:  # pragma: no cover - only without the SDK
            raise JudgeUnavailable("openai SDK is not installed") from exc
        self._client = OpenAI(api_key=self._api_key)
        return self._client

    def judge(self, *, answer: str, facts: Sequence[tuple[str, str]]) -> JudgeVerdict:
        if not facts:
            return _not_faithful(self.stamp, "no cited facts to ground on")
        if not answer or not answer.strip():
            return _not_faithful(self.stamp, "empty answer")
        client = self._ensure_client()
        response = None
        start = time.perf_counter()
        try:
            response = client.responses.parse(
                model=self._model,
                instructions=self._prompt.text,
                input=build_judge_user_message(answer=answer, facts=facts),
                text_format=JudgeDTO,
            )
        except Exception as exc:  # SDK / transport / validation — fail closed
            raise JudgeUnavailable(f"judge call failed: {exc}") from exc
        finally:
            usage_recorder.record(
                agent="judge",
                model=self._model,
                usage=getattr(response, "usage", None),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                success=response is not None,
            )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise JudgeUnavailable("judge returned no parseable structured output")
        return JudgeVerdict(
            faithful=bool(parsed.faithful),
            score=float(parsed.score),
            unsupported_claims=tuple(parsed.unsupported_claims),
            judge=self.stamp,
        )


def build_judge(*, api_key: str | None = None, model: str | None = None, client=None):
    """OpenAI judge when a key (or client) is available, else the keyless twin."""
    key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
    if client is not None or key:
        return OpenAIJudge(
            model=model or os.environ.get("CARELINE_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL,
            api_key=key or None,
            client=client,
        )
    return KeylessJudge()


__all__ = [
    "DEFAULT_JUDGE_MODEL",
    "JudgeDTO",
    "JudgeUnavailable",
    "JudgeVerdict",
    "KeylessJudge",
    "OpenAIJudge",
    "build_judge",
    "build_judge_user_message",
    "load_judge_prompt",
]
