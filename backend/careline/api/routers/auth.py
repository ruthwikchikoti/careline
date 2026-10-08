"""Doctor JWT issuance (NR-6, REVIEW-2, SECURITY-2).

A doctor token is minted only against *that doctor's own* credential — a salted
per-doctor hash in ``CARELINE_DOCTOR_CREDENTIALS`` — never for a bare
``doctor_id``. The shared ``CARELINE_DOCTOR_PASSWORD`` is only an explicit
dev/local-demo fallback (used when no per-doctor table is configured) and is
refused in production / public-demo mode. Unknown id, wrong credential, reserved
demo id, and an id outside the allowlist are all the same 401, so the endpoint is
not an oracle for which check failed.

Brute force (REVIEW-4): every failure counts against the doctor id (5 → locked
for the window, from any IP) **and** against the client IP across *all* doctor ids
(``CARELINE_LOGIN_MAX_FAILURES_PER_IP``, default 20 per 15 min) — so spraying one
guess per doctor id from one address is stopped too. While locked, even the right
password gets a 429. Separately, the login routes have their own per-IP minute
window in the budget guard (``CARELINE_LOGIN_RATE_LIMIT_PER_MINUTE``).

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
    """Issue a doctor JWT after verifying this doctor's own credential."""
    auth_svc = request.app.state.auth_svc
    account = f"doctor:{body.doctor_id}"
    ip = guard_login(request, account=account)
    ok = auth_svc.verify_doctor_credentials(doctor_id=body.doctor_id, password=body.password)
    record_login(request, account=account, ip=ip, ok=ok)
    if not ok:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
    token = auth_svc.issue_doctor_token(body.doctor_id)
    return TokenResponse(access_token=token)
