"""Patient consent — the DPDP gate on recording and processing (NG-3).

India's DPDP Act (2023) requires *explicit, purpose-bound* consent before a
patient's data is recorded or processed, and a standing right to withdraw it. We
model that as a small immutable value object, :class:`Consent`, stamped onto a
consultation at the moment the patient agrees.

Consent is **fail-closed**: the default is *no* consent. A consultation without an
active consent must not be recorded or answered from — the absence of a grant is
treated as a refusal, never as silent permission. Withdrawal is irreversible for a
given grant (you mint a fresh grant to re-consent), so an audit can always tell
*when* processing was and was not authorised.

Owner: Naga (scope ``data``). Naresh's ``ConsultationService`` stamps consent via
this VO; the DPDP erasure path checks it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Consent(BaseModel):
    """An explicit, purpose-bound consent grant (or its withdrawal).

    ``purpose`` records *what* the patient agreed to (e.g. ``"post-consultation
    follow-up answering"``); a grant is only meaningful for that purpose. A grant
    with ``withdrawn_at`` set is retained for audit but is no longer active.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str  # the patient the consent belongs to
    purpose: str
    granted: bool = False
    granted_at: datetime | None = None
    withdrawn_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """True only for a live grant — granted, with a timestamp, not withdrawn.

        Fail-closed: anything short of an explicit, un-withdrawn grant is inactive,
        so missing or partial consent is read as "do not process".
        """
        return self.granted and self.granted_at is not None and self.withdrawn_at is None

    @classmethod
    def grant(cls, *, subject_id: str, purpose: str, at: datetime) -> "Consent":
        """Mint an active consent for ``subject_id`` and ``purpose`` at ``at``."""
        return cls(subject_id=subject_id, purpose=purpose, granted=True, granted_at=at)

    def withdraw(self, at: datetime) -> "Consent":
        """Return a withdrawn copy (the original grant is kept immutable for audit).

        Fail-closed: withdrawing an inactive consent is a no-op error — there is
        nothing live to revoke — so a double-withdraw cannot corrupt the trail.
        """
        if not self.is_active:
            raise ValueError("cannot withdraw a consent that is not active")
        if at < self.granted_at:  # type: ignore[operator]
            raise ValueError("withdrawal cannot precede the grant")
        return self.model_copy(update={"withdrawn_at": at})


__all__ = ["Consent"]
