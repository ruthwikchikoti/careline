"""Temporal value objects for the longitudinal record (NG-1).

Every clinical fact in CareLine is *time-bounded*: it became true at some instant
and may later be retired (superseded) by a newer fact. We model that with a single
half-open interval, :class:`Validity`:

    effective_from <= now < superseded_at

The interval is **half-open on purpose**. The lower bound is inclusive (a fact is
valid the instant it takes effect); the upper bound is exclusive (the instant a
fact is superseded it is *no longer* valid). This makes supersession seamless: a
replacement fact's ``effective_from`` can equal the old fact's ``superseded_at``
with no overlap and no gap — exactly one fact is valid at any instant.

Why this matters for safety: the overriding rule is *never answer from a superseded
fact*. Temporal validity is the mechanism that enforces it — :meth:`is_valid_at`
is a pure, deterministic predicate the valid-slice query and the gate chain both
rely on, so "is this still true?" never depends on an LLM's judgement.

Owner: Naga (scope ``data``). These VOs are a frozen interface the Reasoner is
grounded on and the Gatekeeper checks; downstream code imports them rather than
re-deriving temporal logic.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class Validity(BaseModel):
    """A half-open temporal interval: ``effective_from <= now < superseded_at``.

    An open interval (``superseded_at is None``) means the fact is still in force
    with no known end. A closed interval means the fact has been retired and is
    kept only for history/audit — never surfaced as current truth.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    effective_from: datetime
    superseded_at: datetime | None = None

    @model_validator(mode="after")
    def _check_interval(self) -> "Validity":
        if self.superseded_at is not None and self.superseded_at <= self.effective_from:
            raise ValueError(
                "superseded_at must be strictly after effective_from "
                "(a fact cannot be retired before — or at — the instant it takes effect)"
            )
        return self

    @property
    def is_open(self) -> bool:
        """True when the fact has no known end (still in force)."""
        return self.superseded_at is None

    def is_valid_at(self, now: datetime) -> bool:
        """Half-open membership test: ``effective_from <= now < superseded_at``.

        The single source of temporal truth. Returns False before the fact takes
        effect and at/after the instant it is superseded.
        """
        if now < self.effective_from:
            return False
        if self.superseded_at is not None and now >= self.superseded_at:
            return False
        return True

    def supersede(self, at: datetime) -> "Validity":
        """Return a *new* closed interval ending at ``at`` (the supersession instant).

        Fail-closed: an already-closed interval cannot be re-superseded, and the
        cut instant must fall after ``effective_from`` so the interval stays valid.
        The original is left untouched (these VOs are frozen).
        """
        if self.superseded_at is not None:
            raise ValueError("validity is already superseded")
        if at <= self.effective_from:
            raise ValueError("supersession instant must be after effective_from")
        return self.model_copy(update={"superseded_at": at})


__all__ = ["Validity"]
