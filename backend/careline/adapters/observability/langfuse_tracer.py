"""Langfuse tracing — version-stamped, PHI-safe by omission, honest numbers.

Optional observability: when ``CARELINE_LANGFUSE_PUBLIC_KEY`` and
``CARELINE_LANGFUSE_SECRET_KEY`` are set (and the ``obs`` extra —
``pip install -e ".[obs]"`` — is installed), every QuestionService turn is
reported to Langfuse as one **generation** carrying:

* the REAL model(s) the turn called (from the per-turn usage scope), or the
  caller's label when the turn made no LLM call (e.g. a red-flag escalation);
* the REAL end-to-end latency and time window of the turn. The caller opens
  the turn with :func:`begin_turn` before running the pipeline and passes the
  handle to :func:`record_turn`:

  - Langfuse **v2** (``client.generation(...)``): ``start_time`` = the turn's
    real start, ``end_time`` = now;
  - Langfuse **v3** (``client.start_generation(...)`` → ``update`` → ``end``):
    v3 spans cannot be back-dated, so the generation is OPENED in
    :func:`begin_turn` at the turn's real start and ended with an explicit
    ``end_time`` (ns since epoch). Without a begun turn the v3 span is opened
    at report time; it is still ended explicitly and the real window is in
    metadata (``start_time``/``end_time``/``latency_ms``);
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

Cost scope: the trace is sent when the turn ends, before the online
monitor's sampled async judge runs, so the Langfuse per-turn cost covers the
reasoner/verifier calls; the judge's spend for a sampled turn is added to
that turn's cost in the online monitor (``GET /monitoring``) and is visible
per agent in the usage log / cost report.

Owner: Naresh (scope ``api``/observability).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from careline.adapters.llm.prompt_registry import active_versions
from careline.adapters.llm.usage import TurnUsage

_client_cache: dict[tuple[str, str, str], Any] = {}
#: URLs of traces sent in this process (v3), newest last — for evidence reports.
TRACE_URLS: list[str] = []


def flush() -> None:
    """Block until queued traces are sent (scripts call this before exiting)."""
    for client in list(_client_cache.values()):
        try:
            client.flush()
        except Exception:
            pass


def _patient_hash(patient_id: str) -> str:
    salt = os.environ.get("CARELINE_TRACE_SALT", "careline-trace")
    return hashlib.sha256(f"{salt}:{patient_id}".encode()).hexdigest()[:12]


def langfuse_credentials() -> tuple[str, str, str] | None:
    """(public, secret, host) from the CARELINE_* names, else Langfuse's own.

    Langfuse hands out LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
    LANGFUSE_BASE_URL (or LANGFUSE_HOST); a deployer who pastes those gets
    tracing instead of a silent no-op. The CARELINE_* names win when set.
    """
    env = os.environ.get
    public = env("CARELINE_LANGFUSE_PUBLIC_KEY") or env("LANGFUSE_PUBLIC_KEY")
    secret = env("CARELINE_LANGFUSE_SECRET_KEY") or env("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        return None
    host = (env("CARELINE_LANGFUSE_HOST") or env("LANGFUSE_BASE_URL")
            or env("LANGFUSE_HOST") or "")
    return public, secret, host


def _client() -> Any | None:
    creds = langfuse_credentials()
    if creds is None:
        return None
    public, secret, host = creds
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


@dataclass(frozen=True)
class TurnTrace:
    """Handle for one turn's trace, opened at the turn's real start."""

    start_time: datetime
    generation: Any | None = None  # Langfuse v3 generation opened at start_time
    client: Any | None = None


def begin_turn(*, client: Any | None = None) -> TurnTrace:
    """Mark the start of a turn (call BEFORE running the pipeline). Never raises.

    With a v3 client the generation is opened now, so its span covers the
    real turn; with v2 (or no client) only the start time is captured.
    """
    start = datetime.now(timezone.utc)
    try:
        client = client if client is not None else _client()
        if client is not None and hasattr(client, "start_generation"):
            return TurnTrace(start, client.start_generation(name="careline.turn"), client)
        return TurnTrace(start, None, client)
    except Exception:
        return TurnTrace(start, None, None)


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


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
    trace: TurnTrace | None = None,
) -> bool:
    """Report one turn. Returns True when something was actually sent.

    ``turn_usage`` is the :func:`~careline.adapters.llm.usage.turn_scope` of
    this turn; ``latency_ms`` the caller's measured wall time; ``trace`` the
    :func:`begin_turn` handle (real start time; the open v3 generation).
    ``client`` injects an SDK client (tests). Never raises — tracing must not
    break the clinical call.
    """
    try:
        if client is None and trace is not None:
            client = trace.client
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
        start = (
            trace.start_time
            if trace is not None
            else end - timedelta(milliseconds=max(0.0, latency_ms))
        )
        metadata["start_time"] = start.isoformat()
        metadata["end_time"] = end.isoformat()

        if hasattr(client, "start_generation"):  # Langfuse v3
            details: dict[str, Any] = {
                "model": resolved_model,
                "input": trace_input,
                "output": trace_output,
                "metadata": metadata,
            }
            if usage_counts is not None:
                details["usage_details"] = usage_counts
            if cost is not None:
                details["cost_details"] = {"total": cost}
            generation = trace.generation if trace is not None else None
            if generation is None:
                # Not begun at the turn's start: the span opens now (v3 cannot
                # back-date); the real window is in metadata.
                generation = client.start_generation(name="careline.turn", **details)
            generation.update(**details)
            if os.environ.get("CARELINE_LANGFUSE_PUBLIC_TRACES", "").lower() in ("1", "true"):
                # Opt-in share links for demo evidence (fictional data only):
                # a public trace opens without a Langfuse login.
                generation.update_trace(public=True)
            generation.end(end_time=_ns(end))
            try:
                TRACE_URLS.append(client.get_trace_url(trace_id=generation.trace_id))
            except Exception:
                pass
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


__all__ = ["TRACE_URLS", "TurnTrace", "begin_turn", "flush", "langfuse_credentials", "record_turn"]
