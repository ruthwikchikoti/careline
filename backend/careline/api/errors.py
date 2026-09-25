"""No-leak exception handlers for the FastAPI app (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import logging
import sys
import traceback

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from careline.adapters.auth.internal_key import KeyInvalid
from careline.adapters.auth.jwt import TokenInvalid
from careline.domain.ports.reasoning import ReasonerUnavailable
from careline.services.approval_service import AlreadyApprovedError, NoFactsError
from careline.services.consultation_service import ConsentViolation, ConsultationNotFound
from careline.services.extraction_service import NoTranscriptError
from careline.services.patient_lookup_service import PatientNotFound

logger = logging.getLogger(__name__)


def _json(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def register_exception_handlers(app: FastAPI) -> None:
    """Wire fail-closed, no-leak handlers onto ``app``."""

    @app.exception_handler(TokenInvalid)
    @app.exception_handler(KeyInvalid)
    async def _unauthorized(_request: Request, _exc: Exception) -> JSONResponse:
        return _json(401, "unauthorized")

    @app.exception_handler(PatientNotFound)
    @app.exception_handler(ConsultationNotFound)
    async def _not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return _json(404, "not found")

    @app.exception_handler(ConsentViolation)
    async def _consent(_request: Request, _exc: ConsentViolation) -> JSONResponse:
        return _json(422, "consent required")

    @app.exception_handler(AlreadyApprovedError)
    async def _already_approved(
        _request: Request, _exc: AlreadyApprovedError
    ) -> JSONResponse:
        return _json(409, "already approved")

    @app.exception_handler(NoFactsError)
    async def _no_facts(_request: Request, _exc: NoFactsError) -> JSONResponse:
        return _json(422, "no facts to approve")

    @app.exception_handler(NoTranscriptError)
    async def _no_transcript(_request: Request, _exc: NoTranscriptError) -> JSONResponse:
        return _json(422, "no transcript")

    @app.exception_handler(ReasonerUnavailable)
    async def _reasoner_unavailable(
        _request: Request, _exc: ReasonerUnavailable
    ) -> JSONResponse:
        return _json(503, "reasoning service unavailable")

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _json(500, "internal error")
