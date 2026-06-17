"""Per-call session — clarify-turn enforcement across turns (VI-3).

A ``CallSession`` tracks how many turns (and how many CLARIFY turns) have
happened in a single patient call.  The ConfidenceStaleness gate reads
:meth:`can_clarify` to decide whether the next uncertain turn should be
CLARIFY or ESCALATE: after ``max_clarify_turns`` clarifications, the agent
stops asking follow-ups and hands the call to the doctor.

This enforces the "clarify-then-escalate" pattern from the overriding rule:
uncertainty resolves toward the doctor, not toward an infinite loop of
"could you rephrase that?"

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CallSession(BaseModel):
    """Mutable session state for one patient call.

    Unlike most domain models this is intentionally *not* frozen — it mutates
    as the call progresses (turn count increments).  It lives in the services
    layer (``QuestionService``) and is threaded through gate contexts.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    patient_id: str
    doctor_id: str
    turn_count: int = Field(default=0, ge=0)
    clarify_count: int = Field(default=0, ge=0)
    max_clarify_turns: int = Field(default=2, ge=0)

    def can_clarify(self) -> bool:
        """True if the agent may still ask a follow-up instead of escalating."""
        return self.clarify_count < self.max_clarify_turns

    def record_turn(self) -> None:
        """Increment the turn counter (called once per question)."""
        self.turn_count += 1

    def record_clarify(self) -> None:
        """Increment the clarify counter (called when verdict is CLARIFY)."""
        self.clarify_count += 1


__all__ = ["CallSession"]
