"""The reasoning ports — the boundary the LLM lives behind (SR-1).

CareLine's safety spine never calls an SDK directly. It depends on two abstract
ports — :class:`Reasoner` and :class:`Verifier` — and the factory injects a
concrete adapter (offline heuristic twin, Anthropic, or OpenAI). This is the
hexagonal boundary that lets the backend swap Anthropic↔OpenAI with **zero** domain
change, and lets the whole suite run offline against the heuristic twins.

The single most important contract here is **fail-closed**: every implementation
that cannot produce a trustworthy result MUST raise :class:`ReasonerUnavailable`
rather than return a guess, a partial, or a silently-degraded answer. The Brain
catches it and escalates. An LLM can never quietly lower the safety bar — if it is
unavailable, refuses, or returns something un-parseable, the patient gets a doctor,
not a hallucination.

Owner: Srujan (scope ``llm``). These ABCs are a frozen interface: Ruthwik's graph
nodes and Vinay's gate chain are written against them, not against any vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult


class ReasonerUnavailable(RuntimeError):
    """Raised when a reasoning agent cannot produce a trustworthy structured result.

    Any SDK/transport error, timeout, refusal, or ``None``/un-parseable structured
    output maps to this single exception. It is the fail-closed signal: the Brain
    treats it as "no answer is safe here" and routes the turn to ESCALATE. It is a
    ``RuntimeError`` so an over-broad ``except Exception`` in a caller still trips
    the safe path rather than swallowing it.
    """


class Reasoner(ABC):
    """Classifies a question and proposes a candidate answer from the valid slice.

    The Reasoner is grounded **only** on ``context`` — the doctor's approved,
    currently-valid facts. It must never answer from its own prior knowledge; an
    answer with no supporting fact is a non-answer (see
    :meth:`ClassifierProposal.is_answerable`).
    """

    @abstractmethod
    def propose(self, *, question: str, context: ValidSlice) -> ClassifierProposal:
        """Return a structured proposal for ``question`` grounded in ``context``.

        Raises:
            ReasonerUnavailable: if a trustworthy proposal cannot be produced.
        """
        raise NotImplementedError


class Verifier(ABC):
    """Independently checks that a proposal's candidate answer is grounded.

    The Verifier is a *separate* agent from the Reasoner on purpose: a second,
    adversarial pass that re-derives whether each claim is supported by a cited
    valid fact. Its veto is what stops a confident-but-unsupported candidate from
    ever becoming an ANSWER.
    """

    @abstractmethod
    def verify(
        self,
        *,
        question: str,
        proposal: ClassifierProposal,
        context: ValidSlice,
    ) -> VerificationResult:
        """Return whether ``proposal``'s candidate is fully supported by ``context``.

        Raises:
            ReasonerUnavailable: if a trustworthy verification cannot be produced.
        """
        raise NotImplementedError


__all__ = ["ReasonerUnavailable", "Reasoner", "Verifier"]
