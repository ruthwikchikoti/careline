"""Domain enums shared across the agent spine (RU-2).

These are the closed vocabularies every agent handoff speaks in. They live in the
domain layer because they encode safety semantics, not implementation detail:
the only terminal verdicts, the scope classes the gate chain reasons about, the
kinds of clinical fact the record stores, and the status a trace step can carry.

Owner: Ruthwik (scope ``brain``). Downstream layers (data, llm, safety) import
these rather than redefining them, so the vocabulary cannot drift between agents.
"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """The only ways a turn can terminate.

    ``ESCALATE`` is the fail-closed default: any uncertainty, error, or
    out-of-scope condition resolves here rather than to a guessed answer.
    """

    ANSWER = "answer"
    CLARIFY = "clarify"
    ESCALATE = "escalate"


class ScopeCategory(str, Enum):
    """How the gate chain classifies an incoming question.

    Only ``IN_SCOPE`` is eligible for an ANSWER; every other class biases the
    decision toward CLARIFY or ESCALATE.
    """

    IN_SCOPE = "in_scope"            # answerable from this patient's valid context
    OUT_OF_SCOPE = "out_of_scope"    # the doctor never established this — escalate
    CROSS_CONDITION = "cross_condition"  # spans conditions that must not be merged
    RED_FLAG = "red_flag"            # emergency tripwire — bypass the LLM entirely
    ADMINISTRATIVE = "administrative"  # logistics (timings, billing), not clinical


class FactKind(str, Enum):
    """The kinds of clinical fact stored in the Layer-1 longitudinal record.

    Used by the data layer's ``Fact`` subtypes; declared here so the vocabulary is
    shared with the gate chain (e.g. risk weighting differs by kind).
    """

    MEDICATION = "medication"
    INSTRUCTION = "instruction"      # post-op / care instruction or restriction
    DIAGNOSIS = "diagnosis"
    OBSERVATION = "observation"      # vitals, labs, measured findings
    ALLERGY = "allergy"
    FOLLOW_UP = "follow_up"          # scheduled review / next appointment


class TraceStatus(str, Enum):
    """The outcome a single reasoning-trace step records.

    ``TERMINAL`` marks the step that decided the verdict (e.g. a rail or gate that
    forced ESCALATE); ``SKIPPED`` marks a step short-circuited by an earlier
    terminal step. This is what makes every verdict explainable after the fact.
    """

    PASS = "pass"
    TERMINAL = "terminal"
    SKIPPED = "skipped"


__all__ = ["Verdict", "ScopeCategory", "FactKind", "TraceStatus"]
