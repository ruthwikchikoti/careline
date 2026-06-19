"""LangSmith tracing — observability on the live LLM path (VI-5).

Wraps LangSmith ``RunTree`` when a ``LANGSMITH_API_KEY`` is present; degrades
to a transparent **no-op** when the key is absent.  This means the entire
offline test suite runs without any tracing side-effects, while the live
deployment gets full observability into reasoner→verifier calls.

Usage::

    from careline.adapters.llm.tracing import TracingContext

    with TracingContext("brain.run_question") as tc:
        tc.log_input(question=question, patient_id=pid)
        decision = brain.run_question(...)
        tc.log_output(verdict=decision.verdict.value)

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detect LangSmith availability
# ---------------------------------------------------------------------------

_LANGSMITH_KEY: str | None = os.environ.get("LANGSMITH_API_KEY")
_HAS_LANGSMITH = False
_RunTree: type | None = None

if _LANGSMITH_KEY:
    try:
        from langsmith import RunTree as _LsRunTree  # type: ignore[import-untyped]

        _RunTree = _LsRunTree
        _HAS_LANGSMITH = True
        logger.info("LangSmith tracing enabled (key found)")
    except ImportError:
        logger.debug("LANGSMITH_API_KEY set but langsmith package not installed — tracing disabled")


# ---------------------------------------------------------------------------
# TracingContext
# ---------------------------------------------------------------------------


class TracingContext:
    """A tracing scope that wraps LangSmith ``RunTree`` or does nothing.

    When LangSmith is unavailable (no key or no package), every method is a
    no-op.  This keeps the calling code clean — it never needs to check
    whether tracing is active.
    """

    def __init__(self, name: str, *, run_type: str = "chain") -> None:
        self._name = name
        self._run_type = run_type
        self._run: Any = None
        self._inputs: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}

    def log_input(self, **kwargs: Any) -> None:
        """Record input data for this span."""
        self._inputs.update(kwargs)
        if self._run is not None:
            self._run.inputs = self._inputs

    def log_output(self, **kwargs: Any) -> None:
        """Record output data for this span."""
        self._outputs.update(kwargs)
        if self._run is not None:
            self._run.outputs = self._outputs

    def log_metadata(self, **kwargs: Any) -> None:
        """Attach metadata to this span (e.g. patient_id, doctor_id)."""
        if self._run is not None:
            if not hasattr(self._run, "extra") or self._run.extra is None:
                self._run.extra = {}
            self._run.extra.update(kwargs)

    def _start(self) -> None:
        if _HAS_LANGSMITH and _RunTree is not None:
            try:
                self._run = _RunTree(
                    name=self._name,
                    run_type=self._run_type,
                    inputs=self._inputs,
                )
            except Exception:
                logger.debug("Failed to create LangSmith run — continuing without trace")
                self._run = None

    def _end(self, *, error: str | None = None) -> None:
        if self._run is not None:
            try:
                self._run.outputs = self._outputs
                if error:
                    self._run.error = error
                self._run.end(
                    outputs=self._outputs,
                )
                self._run.post()
            except Exception:
                logger.debug("Failed to post LangSmith run — trace lost")


@contextmanager
def trace_span(
    name: str,
    *,
    run_type: str = "chain",
) -> Generator[TracingContext, None, None]:
    """Context manager that opens a LangSmith span (or no-ops).

    Usage::

        with trace_span("reasoner.propose") as tc:
            tc.log_input(question=q)
            result = reasoner.propose(...)
            tc.log_output(verdict=result.scope.value)
    """
    ctx = TracingContext(name, run_type=run_type)
    ctx._start()
    try:
        yield ctx
    except Exception as exc:
        ctx._end(error=str(exc))
        raise
    else:
        ctx._end()


def is_tracing_enabled() -> bool:
    """True when LangSmith tracing is active (key + package present)."""
    return _HAS_LANGSMITH


__all__ = ["TracingContext", "trace_span", "is_tracing_enabled"]
