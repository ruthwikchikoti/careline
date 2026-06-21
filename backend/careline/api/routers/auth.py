"""Doctor JWT issuance (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from careline.api.dto.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(body: LoginRequest, request: Request) -> TokenResponse:
    """Issue a demo JWT for the requested doctor (capstone login surface)."""
    token = request.app.state.auth_svc.issue_doctor_token(body.doctor_id)
    return TokenResponse(access_token=token)
