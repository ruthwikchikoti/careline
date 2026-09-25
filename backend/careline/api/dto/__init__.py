"""API request/response DTOs (owner: Naresh)."""

from careline.api.dto.auth import LoginRequest, TokenResponse
from careline.api.dto.consultations import (
    ApprovalOut,
    ConsentIn,
    ConsultationCreateIn,
    ConsultationOut,
    ExtractOut,
)
from careline.api.dto.patients import PatientOut

__all__ = [
    "ApprovalOut",
    "ConsentIn",
    "ConsultationCreateIn",
    "ConsultationOut",
    "ExtractOut",
    "LoginRequest",
    "PatientOut",
    "TokenResponse",
]
