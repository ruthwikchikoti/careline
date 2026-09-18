"""The patient aggregate and its valid slice (NG-1).

:class:`Patient` is the longitudinal record: the *full* history of every fact ever
recorded for one patient under one doctor, including retired (superseded) versions.
You never reason over the whole history directly — you ask it for the **valid slice**
at an instant:

    patient.valid_slice(now)  -> only the approved facts valid *right now*

:class:`ValidSlice` is that derived view: the doctor's approved, currently-valid
context — exactly what the Reasoner is grounded on and the Gatekeeper checks. A
superseded fact and an unapproved fact are both absent from it by construction, so
"never answer from a superseded fact" is enforced by the query, not by vigilance.

``doctor_id`` rides on the aggregate because isolation is **one patient per call,
one doctor** — there is no aggregate that spans tenants. (Storage-level structural
isolation is layered on in NG-5; here it is an invariant of the model.)

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from careline.domain.enums import FactKind
from careline.domain.model.fact import Fact


class ValidSlice(BaseModel):
    """The approved facts valid at a single instant — the grounding context.

    Immutable snapshot: it records *what* was valid and *as of when*, so a verdict
    built from it stays explainable after the fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    facts: tuple[Fact, ...] = ()

    @property
    def is_empty(self) -> bool:
        return len(self.facts) == 0

    @property
    def count(self) -> int:
        return len(self.facts)

    @property
    def citations(self) -> list[str]:
        """The ids of the valid facts — what an answer cites as its support."""
        return [f.id for f in self.facts]

    @property
    def summaries(self) -> list[str]:
        """The doctor-approved phrasings available to the answer agent."""
        return [f.summary for f in self.facts]

    def of_kind(self, kind: FactKind) -> tuple[Fact, ...]:
        """The valid facts of one kind (e.g. all current medications)."""
        return tuple(f for f in self.facts if f.kind is kind)


class Patient(BaseModel):
    """The longitudinal record for one patient under one doctor.

    Holds every fact version ever recorded (current and retired). Query it with
    :meth:`valid_slice` for current truth and :meth:`history` for the audit trail;
    grow it functionally with :meth:`with_fact` (the model is frozen).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    patient_id: str
    doctor_id: str
    facts: tuple[Fact, ...] = ()

    def valid_slice(self, now: datetime) -> ValidSlice:
        """The approved facts valid at ``now`` — superseded/unapproved facts dropped."""
        current = tuple(f for f in self.facts if f.is_current(now))
        return ValidSlice(as_of=now, facts=current)

    def history(self, now: datetime) -> tuple[Fact, ...]:
        """Facts retired (superseded) as of ``now``, most-recently-retired first.

        Retained for audit and explanation — never surfaced as current truth.
        """
        retired = [
            f
            for f in self.facts
            if f.validity.superseded_at is not None and f.validity.superseded_at <= now
        ]
        retired.sort(key=lambda f: f.validity.superseded_at, reverse=True)  # type: ignore[arg-type,return-value]
        return tuple(retired)

    def with_fact(self, fact: Fact) -> "Patient":
        """Return a new aggregate with ``fact`` appended (the record is immutable)."""
        return self.model_copy(update={"facts": (*self.facts, fact)})


__all__ = ["ValidSlice", "Patient"]
