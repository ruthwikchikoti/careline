"""Pre-LLM triage — the one deterministic front door shared by Brain and graph.

Every question passes through here before retrieval or any model call:

    red-flag rail → acute-concern net → multi-condition tripwire → small talk

Policy v4 (round-1 adversarial review) moved the acute-concern net here. At
v3 it ran only inside the scope gate's OUT_OF_SCOPE branch, so an emergency
mixed into an in-scope or administrative question ("soft diet avoid spicy; I
want to die", "billing question: my child swallowed my tablets") reached the
Reasoner, matched a diet or follow-up fact, and was ANSWERED or CLARIFIED.
Now both nets run on the raw question for EVERY turn, before small talk and
before the Reasoner; any hit is a terminal ESCALATE.

Why a shared function: the Brain and the LangGraph ``triage`` node both call
:func:`run_triage`, so the two engines cannot drift apart here — the parity
test (RU-5) asserts it, and this makes it true by construction.

Owner: Ruthwik (scope ``brain``).
"""

from __future__ import annotations

from careline.domain.enums import ScopeCategory, TraceStatus
from careline.domain.gates.chain import EMERGENCY_LINE
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.rails.acute_concern import check_acute_concern
from careline.domain.rails.conversational import is_small_talk
from careline.domain.rails.red_flag import check_multi_condition, check_red_flag


def run_triage(question: str, trace: ReasoningTrace) -> Decision | None:
    """Run the pre-LLM rails; return a terminal ``Decision`` or ``None`` to continue.

    Order matters: both emergency nets run before the small-talk rail, so
    "hey, I have chest pain" still escalates.
    """
    # -- Red-flag rail (literal + structural + semantic) ------------------
    matched = check_red_flag(question)
    if matched:
        trace.record(
            "red_flag_rail",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=f"emergency keyword matched: {matched!r}",
        )
        return Decision.escalate(
            f"Emergency symptom detected ({matched}) — transferring to your doctor.",
            scope=ScopeCategory.RED_FLAG,
            risk=1.0,
            trace=trace,
        )

    # -- Acute-concern net (v4: every question, not just out-of-scope) -----
    acute = check_acute_concern(question)
    if acute is not None:
        trace.record(
            "acute_concern_rail",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=f"first-person acute concern ({acute}) — escalating before the reasoner",
        )
        return Decision.escalate(
            "Your message may describe an urgent symptom — transferring to your "
            f"doctor. {EMERGENCY_LINE}",
            scope=ScopeCategory.RED_FLAG,
            risk=0.9,
            trace=trace,
        )

    # -- Multi-condition tripwire -----------------------------------------
    is_cross, groups = check_multi_condition(question)
    if is_cross:
        trace.record(
            "multi_condition_tripwire",
            TraceStatus.TERMINAL,
            spec_section="§5.3",
            detail=f"question spans conditions: {', '.join(groups)}",
        )
        return Decision.escalate(
            "Question spans multiple clinical conditions — transferring to your doctor.",
            scope=ScopeCategory.CROSS_CONDITION,
            risk=0.95,
            trace=trace,
        )

    # -- Small talk → nudge, never escalate -------------------------------
    # A greeting/pleasantry is not a clinical question; without this it would
    # be classified out-of-scope. Runs *after* both emergency nets.
    if is_small_talk(question):
        trace.record(
            "conversational_rail",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail="non-clinical small talk — answering conversationally",
        )
        return Decision.clarify(
            "Hi! I can help with questions about your medicines, diet, or the "
            "care instructions your doctor approved. What would you like to know?",
            scope=ScopeCategory.ADMINISTRATIVE,
            trace=trace,
        )
    return None


__all__ = ["run_triage"]
