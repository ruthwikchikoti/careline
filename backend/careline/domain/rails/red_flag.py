"""Pre-LLM red-flag matcher and multi-condition tripwire (VI-1).

The Triage agent's first line of defense: a hardcoded keyword/phrase matcher
that fires **before** any LLM call.  If a patient's question mentions chest
pain, breathing difficulty, or any other emergency keyword, the turn is
immediately routed to ESCALATE — no model involved, no confidence needed,
fully deterministic.

The multi-condition tripwire detects when a question spans multiple clinical
condition groups (e.g. diabetic + post-op diet), which must escalate because
the agent cannot safely merge guidance across conditions.

Design choice: false positives are *safe* (the doctor handles it); false
negatives are not.  Err on the side of inclusion.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Red-flag patterns (hardcoded emergency keywords)
# ---------------------------------------------------------------------------
# Each pattern is a case-insensitive regex fragment.  They are compiled into a
# single alternation for a single-pass scan of the question.

RED_FLAG_PATTERNS: tuple[str, ...] = (
    r"chest\s+pain",
    r"difficulty\s+breathing",
    r"breathing\s+difficulty",
    r"can'?t\s+breathe",
    r"shortness\s+of\s+breath",
    r"unconscious",
    r"unresponsive",
    r"seizure",
    r"convulsion",
    r"bleeding\s+heavily",
    r"heavy\s+bleeding",
    r"suicid",                  # matches suicide, suicidal
    r"self[- ]?harm",
    r"heart\s+attack",
    r"stroke\s+symptom",
    r"anaphyla",                # anaphylaxis, anaphylactic
    r"choking",
    r"overdose",
    r"poison",
    r"severe\s+allergic",
    r"head\s+injury",
    r"loss\s+of\s+consciousness",
)

_RED_FLAG_RE = re.compile(
    "|".join(f"(?:{p})" for p in RED_FLAG_PATTERNS),
    re.IGNORECASE,
)


def check_red_flag(question: str) -> str | None:
    """Return the matched red-flag phrase if found, else ``None``.

    Pure string matching — deterministic, runs pre-LLM.  A match means the
    turn goes straight to ESCALATE without ever invoking the Reasoner.
    """
    match = _RED_FLAG_RE.search(question)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Multi-condition tripwire
# ---------------------------------------------------------------------------
# Condition keyword groups.  If a question touches ≥2 distinct groups, it
# spans conditions and must escalate (cross-condition interaction is out of
# scope for the agent).

_CONDITION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cardiac", ("heart", "cardiac", "blood pressure", "bp ", "hypertension", "cholesterol")),
    ("diabetes", ("diabetes", "diabetic", "blood sugar", "glucose", "insulin", "metformin")),
    ("surgery", ("surgery", "surgical", "post-op", "post-operative", "operation", "incision")),
    ("respiratory", ("asthma", "inhaler", "breathing", "copd", "respiratory", "nebulizer")),
    ("renal", ("kidney", "renal", "dialysis", "creatinine")),
    ("neurological", ("seizure", "epilepsy", "migraine", "neurological")),
    ("gastric", ("gastric", "ulcer", "acid reflux", "gerd", "stomach")),
    ("psychiatric", ("anxiety", "depression", "psychiatric", "mental health")),
)


def check_multi_condition(question: str) -> tuple[bool, list[str]]:
    """Detect whether a question spans ≥2 distinct clinical condition groups.

    Returns ``(is_cross_condition, matched_groups)``.  If ``True``, the turn
    must escalate — the agent cannot safely merge guidance across conditions.
    """
    q_lower = question.lower()
    matched: list[str] = []
    for group_name, keywords in _CONDITION_GROUPS:
        if any(kw in q_lower for kw in keywords):
            matched.append(group_name)
    return (len(matched) >= 2, matched)


__all__ = [
    "RED_FLAG_PATTERNS",
    "check_red_flag",
    "check_multi_condition",
]
