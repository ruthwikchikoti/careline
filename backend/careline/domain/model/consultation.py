"""The consultation aggregate — the unit Track A extracts facts from (NG-3).

A :class:`Consultation` is one doctor↔patient encounter: a transcript, the
patient's :class:`~careline.domain.model.consent.Consent`, and (once the doctor
approves) the :class:`~careline.domain.model.fact.Fact` set extracted from it. It
is the upstream of the longitudinal record — Naresh's extraction drafts facts onto
a consultation, the doctor one-tap approves, and the approved facts are applied
(with supersession) into the patient's :class:`~careline.domain.model.patient.Patient`.

Two invariants make it safe to build on:

1. **No processing without consent.** :meth:`is_processable` is the single gate the
   pipeline checks before extracting or answering — absent/withdrawn consent means
   the encounter is not processable, full stop (DPDP, fail-closed).
2. **Draft ≠ approved.** Extracted facts sit on a ``draft`` consultation and are
   *not* yet part of the patient's valid context; only the explicit doctor approval
   transition (:meth:`approve`) makes them eligible to be applied to Layer 1.

``doctor_id`` rides on the aggregate so a consultation, like every record in
CareLine, belongs to exactly one tenant.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from careline.domain.model.consent import Consent
from careline.domain.model.fact import Fact

ConsultationStatus = Literal["draft", "approved", "archived"]


class Consultation(BaseModel):
    """One doctor↔patient encounter and the facts drafted/approved from it.

    Grow it functionally (the model is frozen): :meth:`with_facts` attaches drafted
    facts, :meth:`approve` flips the status once the doctor signs off.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    consultation_id: str
    doctor_id: str
    patient_id: str
    created_at: datetime
    status: ConsultationStatus = "draft"
    transcript: str | None = None
    consent: Consent | None = None
    facts: tuple[Fact, ...] = ()

    @property
    def is_processable(self) -> bool:
        """True only when an active consent authorises processing this encounter.

        Fail-closed: no consent object, or a withdrawn one, makes the consultation
        non-processable — the pipeline must not extract from or answer it.
        """
        return self.consent is not None and self.consent.is_active

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    def with_facts(self, facts: tuple[Fact, ...] | list[Fact]) -> "Consultation":
        """Return a copy with ``facts`` attached as the drafted/approved set."""
        return self.model_copy(update={"facts": tuple(facts)})

    def approve(self) -> "Consultation":
        """Flip a draft to ``approved`` (the doctor's one-tap HITL transition).

        Fail-closed: only a processable (consented) draft can be approved, so facts
        can never be promoted off an unconsented or already-finalised encounter.
        """
        if self.status != "draft":
            raise ValueError(f"only a draft consultation can be approved (status={self.status})")
        if not self.is_processable:
            raise ValueError("cannot approve a consultation without active consent")
        return self.model_copy(update={"status": "approved"})


__all__ = ["Consultation", "ConsultationStatus"]
