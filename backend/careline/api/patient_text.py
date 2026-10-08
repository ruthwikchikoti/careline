"""Patient-facing message for a decision — never an internal gate reason.

The gate chain's ``escalation_reason`` is written for the doctor and the audit
("Risk too high (0.78) — escalating to doctor.", "Confidence too low ...").
A patient must never see gate wording or a numeric score, so every surface
that talks to a patient (the portal, the demo console's patient line) shows
this module's plain text instead; the internal reason stays in the audit and
the doctor's escalation queue.

* ESCALATE + RED_FLAG scope -> :data:`PATIENT_EMERGENCY_MESSAGE` (112 first);
* any other ESCALATE -> :data:`PATIENT_ESCALATION_MESSAGE` (with the 112 line);
* ANSWER / CLARIFY -> the decision's own text, unless it carries an internal
  score / gate term, in which case the escalation message (fail closed).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import re

from careline.domain.enums import ScopeCategory, Verdict
from careline.domain.gates.chain import EMERGENCY_LINE

PATIENT_ESCALATION_MESSAGE = (
    "Your question has been sent to your doctor, who will reply here. " + EMERGENCY_LINE
)
PATIENT_EMERGENCY_MESSAGE = (
    "This may be an emergency. " + EMERGENCY_LINE
    + " Your message has also been sent to your doctor."
)

# Gate wording or a score that must never reach a patient.
_INTERNAL = re.compile(
    r"\b\d+\.\d+\b|\b(?:risk|confidence|ceiling|threshold|gate|verifier|reasoner|trace)\b",
    re.IGNORECASE,
)


def _is_red_flag(scope: ScopeCategory | str | None) -> bool:
    if scope is None:
        return False
    value = scope.value if isinstance(scope, ScopeCategory) else str(scope)
    return value == ScopeCategory.RED_FLAG.value


def patient_message(
    verdict: Verdict | str,
    answer_text: str | None,
    escalation_reason: str | None,
    scope: ScopeCategory | str | None,
) -> str | None:
    """The text a patient is shown for one turn (never an internal reason)."""
    value = verdict.value if isinstance(verdict, Verdict) else str(verdict)
    if value == Verdict.ESCALATE.value:
        return PATIENT_EMERGENCY_MESSAGE if _is_red_flag(scope) else PATIENT_ESCALATION_MESSAGE
    text = answer_text
    if not text:
        return PATIENT_ESCALATION_MESSAGE if escalation_reason else None
    if _INTERNAL.search(text):
        return PATIENT_ESCALATION_MESSAGE
    return text


__all__ = ["PATIENT_EMERGENCY_MESSAGE", "PATIENT_ESCALATION_MESSAGE", "patient_message"]
