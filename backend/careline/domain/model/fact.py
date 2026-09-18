"""Clinical facts — the typed contents of the longitudinal record (NG-1).

A :class:`Fact` is the atomic unit the doctor approves and the patient may be
answered from. Every fact carries two safety stamps that the gate chain reads:

1. **Temporal validity** (:class:`~careline.domain.model.temporal.Validity`) — *when*
   the fact is true. Half-open, so a superseded fact silently drops out of the
   valid slice.
2. **Doctor approval** (``approved_by`` / ``approved_at``) — *whether a human
   signed off*. Layer-2 memory may surface a fact, but it only counts as current
   truth if Layer-1 confirms it is **approved and valid now** (:meth:`Fact.is_current`).
   This is "memory proposes, source-of-truth disposes" expressed as one predicate.

The ``kind``-specific subtypes (:class:`Medication`, :class:`Instruction`, …) pin
their :class:`~careline.domain.enums.FactKind` via a ``Literal`` so a Medication
can never be mislabelled as, say, an Allergy. The base :class:`Fact` carries an
explicit doctor-authored ``summary`` — the phrasing actually surfaced in an answer
is the approved phrasing, never auto-generated from structured fields.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from careline.domain.enums import FactKind
from careline.domain.model.temporal import Validity


class Fact(BaseModel):
    """One time-bounded, doctor-approvable clinical statement about a patient.

    Construct the concrete subtype (:class:`Medication`, :class:`Diagnosis`, …)
    rather than the base directly; each subtype fixes its ``kind`` and adds the
    structured clinical fields for that kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: FactKind
    validity: Validity
    summary: str  # the doctor-authored phrasing an answer may surface verbatim
    approved_by: str | None = None  # doctor id who signed off; None = not yet approved
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def _approval_is_coherent(self) -> "Fact":
        # An approval timestamp without an approver is a corrupt stamp — reject it
        # so an unapproved fact can never masquerade as approved.
        if self.approved_at is not None and self.approved_by is None:
            raise ValueError("approved_at requires approved_by (no timestamp without an approver)")
        return self

    @property
    def is_approved(self) -> bool:
        """True once a doctor has signed off on this fact."""
        return self.approved_by is not None

    def is_valid_at(self, now: datetime) -> bool:
        """True when the fact's temporal interval contains ``now`` (half-open)."""
        return self.validity.is_valid_at(now)

    def is_current(self, now: datetime) -> bool:
        """Surfaceable as current truth: **approved AND temporally valid now**.

        The conjunction is the whole safety contract in one line — an unapproved
        fact or a superseded fact is never current, regardless of the other stamp.
        """
        return self.is_approved and self.is_valid_at(now)

    def approve(self, by: str, at: datetime) -> "Fact":
        """Return an approved copy stamped with the doctor id and instant."""
        return self.model_copy(update={"approved_by": by, "approved_at": at})

    def supersede(self, at: datetime) -> "Fact":
        """Return a copy whose validity is closed at ``at`` (retired for history)."""
        return self.model_copy(update={"validity": self.validity.supersede(at)})


class Medication(Fact):
    """A prescribed drug: what to take, how much, how often."""

    kind: Literal[FactKind.MEDICATION] = FactKind.MEDICATION
    name: str
    dose: str | None = None
    frequency: str | None = None
    route: str | None = None


class Instruction(Fact):
    """A post-consultation care instruction or restriction (do / don't)."""

    kind: Literal[FactKind.INSTRUCTION] = FactKind.INSTRUCTION
    text: str


class Diagnosis(Fact):
    """A diagnosed condition (optionally coded)."""

    kind: Literal[FactKind.DIAGNOSIS] = FactKind.DIAGNOSIS
    condition: str
    code: str | None = None


class Observation(Fact):
    """A measured finding — vitals, labs, readings."""

    kind: Literal[FactKind.OBSERVATION] = FactKind.OBSERVATION
    metric: str
    value: str
    unit: str | None = None


class Allergy(Fact):
    """A known allergy and its reaction/severity."""

    kind: Literal[FactKind.ALLERGY] = FactKind.ALLERGY
    substance: str
    reaction: str | None = None
    severity: str | None = None


class FollowUp(Fact):
    """A scheduled review or next appointment."""

    kind: Literal[FactKind.FOLLOW_UP] = FactKind.FOLLOW_UP
    scheduled_for: datetime | None = None
    with_whom: str | None = None


__all__ = [
    "Fact",
    "Medication",
    "Instruction",
    "Diagnosis",
    "Observation",
    "Allergy",
    "FollowUp",
]
