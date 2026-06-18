"""Structural isolation + the safety-critical query builders (NG-5).

This is where "one patient per call, zero cross-patient reachability" becomes
*mechanical*. :func:`scoped_filter` is the only way the repositories build a query,
and it **always** leads with ``doctor_id``. Because there is no query path that does
not start from the tenant, reaching another tenant's data is not a check you can
forget — it is code that does not exist. A cross-patient read therefore returns
nothing → 404, never another patient's row (a leak is sev-0).

On top of the scope, two builders encode the temporal-safety predicates *in the
query itself*, so the database — not Python vigilance — drops unsafe rows:

* :func:`valid_slice_filter` — approved **and** ``effective_from <= now <
  superseded_at`` (half-open). A superseded or unapproved fact is never returned as
  current.
* :func:`history_filter` — the complement: facts already retired as of ``now``, for
  audit/explanation only.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def scoped_filter(
    *, doctor_id: str, patient_id: str | None = None, **extra: Any
) -> dict[str, Any]:
    """Build a tenant-scoped query filter — ``doctor_id`` always leads.

    ``patient_id`` narrows to one patient when given (the call path); omitting it
    scopes to a whole tenant (admin/erasure paths) but never wider. ``extra`` adds
    further predicates *on top of* the mandatory scope — it cannot remove it.
    """
    base: dict[str, Any] = {"doctor_id": doctor_id}
    if patient_id is not None:
        base["patient_id"] = patient_id
    base.update(extra)
    return base


def valid_slice_filter(
    *, doctor_id: str, patient_id: str, now: datetime
) -> dict[str, Any]:
    """Filter for the approved, currently-valid facts at ``now`` (the grounding set).

    Encodes the whole safety contract as a query: scoped to the tenant + patient,
    doctor-approved (``approved_by`` present), taken effect (``effective_from <=
    now``), and not yet superseded (open interval *or* ``superseded_at > now``).
    """
    f = scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
    f["approved_by"] = {"$ne": None}
    f["effective_from"] = {"$lte": now}
    f["$or"] = [{"superseded_at": None}, {"superseded_at": {"$gt": now}}]
    return f


def history_filter(
    *, doctor_id: str, patient_id: str, now: datetime
) -> dict[str, Any]:
    """Filter for facts already retired (superseded) as of ``now`` — audit only."""
    f = scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
    f["superseded_at"] = {"$ne": None, "$lte": now}
    return f


__all__ = ["scoped_filter", "valid_slice_filter", "history_filter"]
