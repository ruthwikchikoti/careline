"""Combined dev entrypoint — real product API + demo console on one port (RU-6).

The web UI uses a single base URL (``NEXT_PUBLIC_API_BASE``). In production the
Live Console would call the authenticated ``/internal/run-question`` against a
registered patient; for a zero-setup demo it calls the keyless ``/demo/*``
endpoints instead. This module serves **both** from one FastAPI app so the entire
UI — login, patients, consultations, audit, *and* the Live Console — works
against ``localhost:8000`` without juggling two servers.

    cd backend && uvicorn careline.combined:app --factory --reload

Owner: Ruthwik (scope ``graph``/integration). Wraps Naresh's ``create_app`` —
does not modify it — and mounts the demo router from ``demo_server``.
"""

from __future__ import annotations

from careline.api.app import create_app
from careline.demo_server import demo_ask, demo_patient


def app():
    """Factory: the real app plus the demo console endpoints."""
    application = create_app()
    application.add_api_route("/demo/patient", demo_patient, methods=["GET"], tags=["demo"])
    application.add_api_route("/demo/ask", demo_ask, methods=["POST"], tags=["demo"])
    return application


__all__ = ["app"]
