"""The §B.6 supersession write path — planned as a pure function (NG-5).

Applying a new clinical fact must never leave *two* current versions of the same
thing (two "active" doses of one drug, two live allergy records for one substance).
:func:`plan_supersession` computes, deterministically and with no I/O, exactly what
a write should do: which currently-valid facts to **close** at ``now`` and which new
facts to **insert** (taking effect at ``now``). The repository then executes that
plan atomically (NG-6); separating the *decision* from the *write* makes the
safety-critical logic unit-testable without a database.

Supersession is keyed on **clinical identity**, not text. A new ``Medication`` named
"Amoxicillin" retires the currently-valid "Amoxicillin"; a new allergy to penicillin
retires the prior penicillin allergy. For kinds with no safe natural key
(``Instruction``: free text; ``FollowUp``: not 1:1) we **do not** auto-supersede —
the new fact is simply added. That is the fail-safe choice: a stale-vs-new
instruction left coexisting surfaces as a *contradiction* the gate chain escalates,
which is safe; silently retiring the wrong instruction would not be.

The handover is seamless half-open: the old fact closes at ``now`` and the new one
takes effect at ``now`` — no overlap, no gap, exactly one valid at every instant.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from careline.domain.enums import FactKind
from careline.domain.model.fact import Fact


def _identity(fact: Fact) -> str | None:
    """The clinical key a new fact supersedes on, or ``None`` for add-only kinds."""
    if fact.kind is FactKind.MEDICATION:
        return fact.name.lower().strip()  # type: ignore[attr-defined]
    if fact.kind is FactKind.ALLERGY:
        return fact.substance.lower().strip()  # type: ignore[attr-defined]
    if fact.kind is FactKind.DIAGNOSIS:
        code = getattr(fact, "code", None)
        return (code or fact.condition).lower().strip()  # type: ignore[attr-defined]
    if fact.kind is FactKind.OBSERVATION:
        return fact.metric.lower().strip()  # type: ignore[attr-defined]
    # Instruction / FollowUp: no safe natural key → add-only (never auto-supersede).
    return None


@dataclass(frozen=True)
class SupersessionPlan:
    """What an :meth:`apply_facts` call should do — decided, not yet executed.

    ``to_close`` are the ids of currently-valid facts to retire at ``now``;
    ``to_insert`` are the new facts re-stamped to take effect at ``now``.
    """

    to_close: tuple[str, ...]
    to_insert: tuple[Fact, ...]


def plan_supersession(
    *, current: tuple[Fact, ...], incoming: tuple[Fact, ...], now: datetime
) -> SupersessionPlan:
    """Decide the supersession write for ``incoming`` against the ``current`` valid set.

    ``current`` should be the patient's currently-valid facts. For each incoming fact
    with a clinical identity that matches a current fact of the same kind, that
    current fact is marked to close at ``now``; every incoming fact is re-stamped to
    take effect at ``now`` (and left open).
    """
    # Index current valid facts by (kind, identity) for O(1) conflict lookup.
    by_identity: dict[tuple[FactKind, str], Fact] = {}
    for cf in current:
        ident = _identity(cf)
        if ident is not None:
            by_identity[(cf.kind, ident)] = cf

    to_close: list[str] = []
    to_insert: list[Fact] = []
    for nf in incoming:
        ident = _identity(nf)
        if ident is not None:
            match = by_identity.get((nf.kind, ident))
            # Only close a fact that genuinely predates the handover instant; a
            # match that already starts at/after ``now`` cannot be cleanly closed.
            if (
                match is not None
                and match.id != nf.id
                and match.validity.effective_from < now
            ):
                to_close.append(match.id)
        # Re-stamp the new fact so it takes effect exactly at the handover instant.
        restamped = nf.model_copy(
            update={"validity": nf.validity.model_copy(
                update={"effective_from": now, "superseded_at": None}
            )}
        )
        to_insert.append(restamped)

    # De-dupe close ids while preserving order (two incoming facts could target one).
    seen: set[str] = set()
    deduped = tuple(i for i in to_close if not (i in seen or seen.add(i)))
    return SupersessionPlan(to_close=deduped, to_insert=tuple(to_insert))


__all__ = ["SupersessionPlan", "plan_supersession"]
