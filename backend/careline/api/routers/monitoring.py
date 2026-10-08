"""Online monitoring read route — ``GET /monitoring``.

Doctor-authenticated projection of the process-wide
:class:`~careline.services.online_monitor.OnlineMonitor` snapshot:
operational, output, quality (online LLM-as-judge), input drift and cost.
The snapshot is aggregate-only by construction — no question text, no patient
identifiers, no fact text — and it is **process-wide**, not per tenant: every
authenticated doctor sees the deployment's aggregate volume and verdict mix.
The response says so in a top-level ``scope`` field rather than implying a
per-doctor view. It adds no safety logic.

Mounted in ``api/app.py`` with ``app.include_router(monitoring_router)``.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.services import online_monitor

router = APIRouter(tags=["monitoring"])

#: Stated on every response: a deployment-wide operator view, never a tenant slice.
MONITORING_SCOPE = "process-wide, aggregate, no PHI"


@router.get("/monitoring")
async def get_monitoring(
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> dict[str, Any]:
    """The live monitoring snapshot (all five categories + alerts)."""
    return {"scope": MONITORING_SCOPE, **online_monitor.snapshot()}


__all__ = ["router"]
