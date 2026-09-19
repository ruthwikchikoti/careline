"""The ``Decision`` and its reasoning trace (RU-2).

This is the structured object every live turn terminates with — the frozen handoff
the Brain produces, the LangGraph spine threads, and the API serialises. It carries
*both* the verdict and the explainable trail of why: which rail/gate fired, in which
order, enforcing which spec section. A verdict you cannot explain is, for a safety
system, a verdict you cannot ship.

Owner: Ruthwik (scope ``brain``). The shape is a frozen interface other members
depend on, so it is deliberately small and constructed through the three named
factories (:meth:`Decision.answer` / :meth:`Decision.clarify` /
:meth:`Decision.escalate`) rather than ad-hoc.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory, TraceStatus, Verdict


class TraceStep(BaseModel):
    """One step in the decision pipeline (a rail, a gate, an agent node)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="The rail/gate/agent that ran, e.g. 'red_flag_rail'.")
    status: TraceStatus = Field(..., description="pass / terminal / skipped.")
    spec_section: str | None = Field(
        default=None, description="The PRD/spec section this step enforces, e.g. '§5.1'."
    )
    detail: str | None = Field(default=None, description="Human-readable reason for the outcome.")

    @property
    def is_terminal(self) -> bool:
        return self.status is TraceStatus.TERMINAL


class ReasoningTrace(BaseModel):
    """The ordered record of every step taken to reach a verdict.

    Append steps with :meth:`record`; the trace is explainable by construction and
    knows whether a terminal step has already fired (so later steps are SKIPPED).
    """

    model_config = ConfigDict(extra="forbid")

    steps: list[TraceStep] = Field(default_factory=list)

    def record(
        self,
        name: str,
        status: TraceStatus,
        *,
        spec_section: str | None = None,
        detail: str | None = None,
    ) -> "ReasoningTrace":
        """Append a step and return self (fluent)."""
        self.steps.append(
            TraceStep(name=name, status=status, spec_section=spec_section, detail=detail)
        )
        return self

    @property
    def terminated(self) -> bool:
        """True once a terminal step has fired — the verdict is decided."""
        return any(step.is_terminal for step in self.steps)

    @property
    def terminal_step(self) -> TraceStep | None:
        """The step that decided the verdict, if any."""
        return next((s for s in self.steps if s.is_terminal), None)


class Decision(BaseModel):
    """The terminal output of a turn: a verdict plus its explainable trace.

    Construct via the named factories rather than the raw initialiser so the
    invariants hold (an ANSWER always carries text; an ESCALATE always carries a
    reason; confidence/risk stay in ``[0, 1]``).
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    answer_text: str | None = None
    escalation_reason: str | None = None
    scope: ScopeCategory | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    citations: list[str] = Field(
        default_factory=list, description="IDs of the valid facts that support an answer."
    )
    trace: ReasoningTrace = Field(default_factory=ReasoningTrace)

    # -- named factories: the only sanctioned ways to build a Decision ----------

    @classmethod
    def answer(
        cls,
        text: str,
        *,
        confidence: float,
        risk: float = 0.0,
        scope: ScopeCategory = ScopeCategory.IN_SCOPE,
        citations: list[str] | None = None,
        trace: ReasoningTrace | None = None,
    ) -> "Decision":
        if not text or not text.strip():
            raise ValueError("an ANSWER must carry non-empty answer_text")
        return cls(
            verdict=Verdict.ANSWER,
            answer_text=text,
            scope=scope,
            confidence=confidence,
            risk=risk,
            citations=list(citations or []),
            trace=trace or ReasoningTrace(),
        )

    @classmethod
    def clarify(
        cls,
        prompt: str,
        *,
        confidence: float = 0.0,
        scope: ScopeCategory | None = None,
        trace: ReasoningTrace | None = None,
    ) -> "Decision":
        if not prompt or not prompt.strip():
            raise ValueError("a CLARIFY must carry a non-empty clarifying prompt")
        return cls(
            verdict=Verdict.CLARIFY,
            answer_text=prompt,
            scope=scope,
            confidence=confidence,
            trace=trace or ReasoningTrace(),
        )

    @classmethod
    def escalate(
        cls,
        reason: str,
        *,
        scope: ScopeCategory | None = None,
        risk: float = 0.0,
        trace: ReasoningTrace | None = None,
    ) -> "Decision":
        if not reason or not reason.strip():
            raise ValueError("an ESCALATE must carry a non-empty reason")
        return cls(
            verdict=Verdict.ESCALATE,
            escalation_reason=reason,
            scope=scope,
            risk=risk,
            trace=trace or ReasoningTrace(),
        )

    @property
    def is_terminal_escalation(self) -> bool:
        return self.verdict is Verdict.ESCALATE


__all__ = ["TraceStep", "ReasoningTrace", "Decision"]
