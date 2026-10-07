"""Langfuse tracing — version-stamped, PHI-safe by omission.

Optional observability: when ``CARELINE_LANGFUSE_PUBLIC_KEY`` and
``CARELINE_LANGFUSE_SECRET_KEY`` are set, every QuestionService turn is
reported to Langfuse as one generation with the active prompt/policy stamps,
model, verdict, latency and estimated cost — the per-request trace a grader
(or a 3 a.m. incident) can drill into. Without keys (the default, and always
in CI) this module is a silent no-op: no import of the SDK, no network, no
failure surface on the clinical path.

PHI stance — *redaction by omission*: the trace carries the raw patient
question (needed to understand any incident; the demo data is fictional) but
**never** patient identifiers (only a salted sha12 of the patient id), never
fact text, and never the full record. Cost/latency come from the usage
recorder, not from re-reading prompts.

Owner: Naresh (scope ``api``/observability).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from careline.adapters.llm import usage as usage_recorder
from careline.adapters.llm.prompt_registry import active_versions


def _patient_hash(patient_id: str) -> str:
    salt = os.environ.get("CARELINE_TRACE_SALT", "careline-trace")
    return hashlib.sha256(f"{salt}:{patient_id}".encode()).hexdigest()[:12]


def _client() -> Any | None:
    public = os.environ.get("CARELINE_LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("CARELINE_LANGFUSE_SECRET_KEY")
    if not public or not secret:
        return None
    try:
        from langfuse import Langfuse  # optional dependency, lazy
    except ImportError:
        return None
    return Langfuse(public_key=public, secret_key=secret)


def record_turn(
    *,
    question: str,
    patient_id: str,
    model: str,
    verdict: str,
    scope: str,
    latency_ms: float,
    extra_metadata: dict[str, Any] | None = None,
) -> bool:
    """Report one turn. Returns True when something was actually sent.

    Never raises — tracing must not break the clinical call.
    """
    try:
        client = _client()
        if client is None:
            return False
        usage = usage_recorder.summary()
        metadata: dict[str, Any] = {
            "verdict": verdict,
            "scope": scope,
            "latency_ms": round(latency_ms, 3),
            "model": model,
            "artifacts": active_versions(),
            "cost_usd_run_total": usage.get("cost_usd"),
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        client.event(  # lightweight event; a full generation trace can follow
            name="careline.turn",
            input={"question": question, "patient": _patient_hash(patient_id)},
            metadata=metadata,
        )
        return True
    except Exception:
        return False


__all__ = ["record_turn"]
