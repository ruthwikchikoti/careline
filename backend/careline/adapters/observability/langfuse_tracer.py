"""Langfuse tracing — version-stamped, PHI-safe by omission, honest numbers.

Optional observability: when ``CARELINE_LANGFUSE_PUBLIC_KEY`` and
``CARELINE_LANGFUSE_SECRET_KEY`` are set (and the ``obs`` extra —
``pip install -e ".[obs]"`` — is installed), every QuestionService turn is
reported to Langfuse as one **generation** carrying:

* the REAL model(s) the turn called (from the per-turn usage scope), or the
  caller's label when the turn made no LLM call (e.g. a red-flag escalation);
* the REAL end-to-end latency the caller measured (start/end time are set so
  Langfuse's own latency column is right, and it is in metadata too);
* PER-TURN tokens and estimated cost from
  :func:`careline.adapters.llm.usage.turn_scope` — never the process-wide
  running total. Without a turn scope the cost is reported as unknown
  (``None``), not guessed.

Works with the Langfuse v2 SDK (``client.generation(...)``) and v3
(``client.start_generation(...)`` → ``end()``). ``CARELINE_LANGFUSE_HOST``
selects a self-hosted instance. Without keys (the default, and always in CI)
this module is a silent no-op: no SDK import, no network, no failure surface
on the clinical path.

PHI stance — *redaction by omission*: the trace carries the raw patient
question (needed to understand any incident; the demo data is fictional) but
**never** patient identifiers (only a salted sha12 of the patient id), never
fact text, and never the full record.

Owner: Naresh (scope ``api``/observability).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from careline.adapters.llm.prompt_registry import active_versions
from careline.adapters.llm.usage import TurnUsage

_client_cache: dict[tuple[str, str, str], Any] = {}


def _patient_hash(patient_id: str) -> str:
    salt = os.environ.get("CARELINE_TRACE_SALT", "careline-trace")
    return hashlib.sha256(f"{salt}:{patient_id}".encode()).hexdigest()[:12]


def _client() -> Any | None:
    public = os.environ.get("CARELINE_LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("CARELINE_LANGFUSE_SECRET_KEY")
    if not public or not secret:
        return None
    host = os.environ.get("CARELINE_LANGFUSE_HOST", "")
    key = (public, secret, host)
    if key in _client_cache:  # one SDK client (and its flush thread) per process
        return _client_cache[key]
    try:
        from langfuse import Langfuse  # optional dependency (obs extra), lazy
    except ImportError:
        return None
    kwargs: dict[str, Any] = {"public_key": public, "secret_key": secret}
    if host:
        kwargs["host"] = host
    _client_cache[key] = Langfuse(**kwargs)
    return _client_cache[key]


def _resolve_model(model: str | None, turn_usage: TurnUsage | None) -> str:
    if turn_usage is not None and turn_usage.models:
        return ",".join(turn_usage.models)  # what was actually called wins
    return model or "no-llm-call"


def record_turn(
    *,
    question: str,
    patient_id: str,
    verdict: str,
    scope: str,
    latency_ms: float,
    model: str | None = None,
    turn_usage: TurnUsage | None = None,
    extra_metadata: dict[str, Any] | None = None,
    client: Any | None = None,
) -> bool:
    """Report one turn. Returns True when something was actually sent.

    ``turn_usage`` is the :func:`~careline.adapters.llm.usage.turn_scope` of
    this turn; ``latency_ms`` the caller's measured wall time. ``client``
    injects an SDK client (tests). Never raises — tracing must not break the
    clinical call.
    """
    try:
        client = client if client is not None else _client()
        if client is None:
            return False
        resolved_model = _resolve_model(model, turn_usage)
        cost = turn_usage.cost_usd if turn_usage is not None else None
        usage_counts = (
            {
                "input": turn_usage.input_tokens,
                "output": turn_usage.output_tokens,
                "total": turn_usage.total_tokens,
            }
            if turn_usage is not None
            else None
        )
        metadata: dict[str, Any] = {
            "verdict": verdict,
            "scope": scope,
            "latency_ms": round(latency_ms, 3),
            "model": resolved_model,
            "artifacts": active_versions(),
            "cost_usd": cost,  # per turn; None = unknown, never a running total
            "llm_calls": turn_usage.calls if turn_usage is not None else None,
            "llm_failed_calls": turn_usage.failed_calls if turn_usage is not None else None,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        trace_input = {"question": question, "patient": _patient_hash(patient_id)}
        trace_output = {"verdict": verdict, "scope": scope}
        end = datetime.now(timezone.utc)
        start = end - timedelta(milliseconds=max(0.0, latency_ms))

        if hasattr(client, "start_generation"):  # Langfuse v3
            kwargs: dict[str, Any] = {
                "name": "careline.turn",
                "model": resolved_model,
                "input": trace_input,
                "metadata": metadata,
            }
            if usage_counts is not None:
                kwargs["usage_details"] = usage_counts
            if cost is not None:
                kwargs["cost_details"] = {"total": cost}
            generation = client.start_generation(**kwargs)
            generation.update(output=trace_output)
            generation.end()
            return True

        if hasattr(client, "generation"):  # Langfuse v2
            usage_payload: dict[str, Any] = dict(usage_counts or {})
            if usage_counts is not None:
                usage_payload["unit"] = "TOKENS"
            if cost is not None:
                usage_payload["total_cost"] = cost
            client.generation(
                name="careline.turn",
                model=resolved_model,
                input=trace_input,
                output=trace_output,
                metadata=metadata,
                usage=usage_payload,
                start_time=start,
                end_time=end,
            )
            return True

        client.event(  # oldest SDKs: a plain event still carries the metadata
            name="careline.turn", input=trace_input, metadata=metadata
        )
        return True
    except Exception:
        return False


__all__ = ["record_turn"]
