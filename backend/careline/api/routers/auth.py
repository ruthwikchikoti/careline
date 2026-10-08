"""Doctor JWT issuance (NR-6, REVIEW-2).

A doctor token is minted only against the per-deployment credential
(``CARELINE_DOCTOR_PASSWORD``) — never for a bare ``doctor_id``. Unknown id, wrong
credential, reserved demo id, and an id outside the allowlist are all the same
401, so the endpoint is not an oracle for which check failed. Repeated failures
lock the doctor id / client IP out with a 429 (REVIEW-4).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from careline.api.dto.auth import LoginRequest, TokenResponse
from careline.api.login_throttle import guard_login, record_login

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = "invalid doctor id or password"


@router.post("/token", response_model=TokenResponse)
def issue_token(body: LoginRequest, request: Request) -> TokenResponse:
    """Issue a doctor JWT after verifying the deployment credential."""
    auth_svc = request.app.state.auth_svc
    account = f"doctor:{body.doctor_id}"
    ip = guard_login(request, account=account)
    ok = auth_svc.verify_doctor_credentials(doctor_id=body.doctor_id, password=body.password)
    record_login(request, account=account, ip=ip, ok=ok)
    if not ok:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
    token = auth_svc.issue_doctor_token(body.doctor_id)
    return TokenResponse(access_token=token)
