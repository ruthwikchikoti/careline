"""API routers (owner: Naresh)."""

from careline.api.routers.auth import router as auth_router
from careline.api.routers.brain import router as brain_router
from careline.api.routers.consultations import router as consultations_router
from careline.api.routers.observability import router as observability_router
from careline.api.routers.patients import router as patients_router

__all__ = [
    "auth_router",
    "brain_router",
    "consultations_router",
    "observability_router",
    "patients_router",
]
