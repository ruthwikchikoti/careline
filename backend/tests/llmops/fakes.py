"""Fake OpenAI clients for the keyless LLMOps tests — no network, no key.

``OracleOpenAI`` mimics ``client.responses.parse`` for the three structured
shapes the system sends (Reasoner ``ProposalDTO``, Verifier
``VerificationDTO``, Judge ``JudgeDTO``). The reasoner side answers from the
eval labels (``must_cite``) so the LLM-slice gate can be exercised end to end
on the real Brain; every other knob is a constructor flag so a test can make
the "model" wrong, unfaithful, or broken.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from careline.adapters.llm.judge import JudgeDTO
from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.enums import ScopeCategory

_FACT_LINE = re.compile(r"^\[(?P<id>[^\]]+)\] \([a-z_]+\) (?P<summary>.+)$", re.MULTILINE)


def _question(user_message: str) -> str:
    return user_message.rsplit("PATIENT QUESTION:\n", 1)[-1].strip()


class _Responses:
    def __init__(self, owner: "OracleOpenAI") -> None:
        self._owner = owner

    def parse(self, *, model, instructions, input, text_format):  # noqa: A002 - SDK name
        return self._owner._parse(model=model, instructions=instructions, user=input,
                                  text_format=text_format)


class OracleOpenAI:
    """A scripted stand-in for ``openai.OpenAI``.

    ``answers``: question -> list of fact ids the reasoner should cite (the
    eval's ``must_cite``). Questions not in the map are declined
    (out_of_scope, no candidate) — the safe behaviour.
    """

    def __init__(
        self,
        answers: dict[str, list[str]] | None = None,
        *,
        judge_faithful: bool = True,
        raise_on: tuple[str, ...] = (),
        tokens: tuple[int, int] = (300, 60),
    ) -> None:
        self.answers = answers or {}
        self.judge_faithful = judge_faithful
        self.raise_on = raise_on  # DTO class names that blow up
        self.tokens = tokens
        self.calls: list[str] = []
        self.responses = _Responses(self)

    def _usage(self):
        return SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1])

    def _parse(self, *, model, instructions, user, text_format):
        name = text_format.__name__
        self.calls.append(name)
        if name in self.raise_on:
            raise RuntimeError(f"simulated provider failure ({name})")
        facts = {m["id"]: m["summary"] for m in _FACT_LINE.finditer(user)}
        if text_format is ProposalDTO:
            want = [f for f in self.answers.get(_question(user), []) if f in facts]
            if want:
                parsed = ProposalDTO(
                    scope=ScopeCategory.IN_SCOPE,
                    candidate_answer=" ".join(facts[f] for f in want),
                    citations=want,
                    confidence=0.95,
                    risk=0.05,
                    rationale="oracle",
                )
            else:
                parsed = ProposalDTO(scope=ScopeCategory.OUT_OF_SCOPE, rationale="oracle decline")
        elif text_format is VerificationDTO:
            parsed = VerificationDTO(supported=True, confidence=0.95)
        elif text_format is JudgeDTO:
            parsed = JudgeDTO(
                faithful=self.judge_faithful,
                score=1.0 if self.judge_faithful else 0.2,
                unsupported_claims=[] if self.judge_faithful else ["made-up claim"],
            )
        else:  # pragma: no cover - unexpected shape
            raise AssertionError(name)
        return SimpleNamespace(output_parsed=parsed, usage=self._usage())


def oracle_from_cases(cases: list[dict], **kwargs) -> OracleOpenAI:
    answers = {
        c["question"]: list(c["expected"].get("must_cite", []))
        for c in cases
        if c["split"] == "in_scope" and c["expected"]["verdict"] == "answer"
    }
    return OracleOpenAI(answers, **kwargs)
