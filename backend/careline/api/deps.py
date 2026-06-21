"""FastAPI dependencies — principals are the only trusted doctor_id source (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from careline.adapters.auth.internal_key import KeyInvalid
from careline.adapters.auth.jwt import TokenInvalid
from careline.adapters.auth.principals import DoctorPrincipal, InternalPrincipal


def get_current_doctor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> DoctorPrincipal:
    """Decode a bearer JWT via :attr:`app.state.auth_svc`."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        return request.app.state.auth_svc.authenticate_doctor(token)
    except TokenInvalid:
        raise HTTPException(status_code=401, detail="unauthorized") from None


def get_internal_principal(
    request: Request,
    x_internal_key: Annotated[str | None, Header(alias="X-Internal-Key")] = None,
) -> InternalPrincipal:
    """Verify the internal API key via :attr:`app.state.auth_svc`."""
    if x_internal_key is None or not x_internal_key.strip():
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        return request.app.state.auth_svc.authenticate_internal(x_internal_key)
    except KeyInvalid:
        raise HTTPException(status_code=401, detail="unauthorized") from None
