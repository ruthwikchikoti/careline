"""Safety thresholds — the numeric knobs the gate chain reads (VI-3).

A single, frozen source of truth for every threshold the 5-gate chain checks.
Safe defaults are baked in; ``Settings.to_thresholds()`` (Naresh NR-1) may
override them from environment/config, but the defaults alone guarantee the
offline suite passes and the safety invariant holds.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Thresholds(BaseModel):
    """Numeric safety thresholds for the gate chain.

    Every field has a safe default.  ``confidence_floor`` is the minimum score
    to consider answering; ``risk_ceiling`` is the maximum risk that still
    allows an answer; ``max_clarify_turns`` caps how many times the agent can
    ask for clarification before escalating.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    confidence_floor: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to attempt an ANSWER; below this → CLARIFY.",
    )
    risk_ceiling: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Maximum risk that still permits an ANSWER; above this → ESCALATE.",
    )
    max_clarify_turns: int = Field(
        default=2,
        ge=0,
        description="After this many CLARIFYs in one call, escalate to the doctor.",
    )


# The default thresholds — used by the offline test suite and any caller
# that does not inject overrides.
DEFAULT_THRESHOLDS = Thresholds()


__all__ = ["Thresholds", "DEFAULT_THRESHOLDS"]
