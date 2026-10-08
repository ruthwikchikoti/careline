"""Doctor JWT issuance (NR-6, REVIEW-2).

A doctor token is minted only against the per-deployment credential
(``CARELINE_DOCTOR_PASSWORD``) — never for a bare ``doctor_id``. Unknown id, wrong
credential, reserved demo id, and an id outside the allowlist are all the same
401, so the endpoint is not an oracle for which check failed.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from careline.api.dto.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = "invalid doctor id or password"


@router.post("/token", response_model=TokenResponse)
def issue_token(body: LoginRequest, request: Request) -> TokenResponse:
    """Issue a doctor JWT after verifying the deployment credential."""
    auth_svc = request.app.state.auth_svc
    if not auth_svc.verify_doctor_credentials(
        doctor_id=body.doctor_id, password=body.password
    ):
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
    token = auth_svc.issue_doctor_token(body.doctor_id)
    return TokenResponse(access_token=token)
