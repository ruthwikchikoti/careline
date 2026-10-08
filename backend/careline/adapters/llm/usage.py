"""Per-request token & cost capture — the "$/request" half of the numbers.

Every live LLM call the adapters make is recorded here: model, tokens,
latency, estimated cost at a versioned price table, the active prompt/policy
stamps, and the agent that made the call. ``scripts/cost_report.py``, the
Langfuse tracer and the online monitor consume these records. No network, no
key — recording never blocks or fails a clinical call (a broken metric must
not become a broken diagnosis).

Two views:

* **Process window** — :func:`records` / :func:`summary` over a bounded ring
  buffer (``CARELINE_USAGE_BUFFER``, default 10 000 records) so a long-lived
  server cannot grow without bound. ``calls_total`` is a
  lifetime counter that survives eviction.
* **Per turn** — :func:`turn_scope` collects exactly the calls made inside one
  QuestionService turn (a ``ContextVar``, so concurrent requests on other
  threads never bleed in). This is the honest per-request number: never a
  process-wide running total presented as one turn's cost. Work a turn hands
  to another thread joins the turn only when run inside a copy of the turn's
  context (``contextvars.copy_context().run``) — the online monitor's async
  judge does exactly that, so judge spend is part of the turn that sampled it.

Prices are USD per 1M tokens (input, output), as of 2026-10. Unknown models
record ``cost_usd=None`` rather than a guess — the report says "unknown
price" instead of inventing a number.

Owner: Srujan (scope ``llm``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO

from careline.adapters.llm.prompt_registry import active_versions

# (input, output) USD per 1M tokens. Update with the as-of date when prices change.
# Anthropic rates: first-party list prices (claude-haiku-4-5 is $1/$5 — the
# earlier $0.80/$4 entry was the Haiku 3.5 price).
PRICE_TABLE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}
PRICE_TABLE_AS_OF = "2026-10"

DEFAULT_CAPACITY = 10_000

_log = logging.getLogger(__name__)


def _capacity_from_env() -> int:
    """``CARELINE_USAGE_BUFFER`` as a positive int; a bad value is the default + a warning.

    Parsed at import — an ops typo must never stop the service from starting.
    """
    raw = os.environ.get("CARELINE_USAGE_BUFFER")
    if raw is None or not raw.strip():
        return DEFAULT_CAPACITY
    try:
        value = int(raw.strip())
    except ValueError:
        value = 0
    if value < 1:
        _log.warning(
            "CARELINE_USAGE_BUFFER=%r is not a positive integer; using the default %d",
            raw, DEFAULT_CAPACITY,
        )
        return DEFAULT_CAPACITY
    return value


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD for one call, or ``None`` when the model isn't in the table."""
    prices = PRICE_TABLE_USD_PER_MTOK.get(model)
    if prices is None:
        return None
    in_price, out_price = prices
    return round(
        (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price, 6
    )


@dataclass(frozen=True)
class UsageRecord:
    agent: str                  # "reasoner" | "verifier" | "extractor" | "judge" | ...
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float | None
    artifacts: dict[str, str]   # e.g. {"reasoner": "reasoner@v1+…", "red_flags": "…"}
    success: bool = True        # False = provider call failed; excluded from cost/latency stats

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TurnUsage:
    """The LLM calls made during one turn (see :func:`turn_scope`)."""

    records: list[UsageRecord] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.records)

    @property
    def failed_calls(self) -> int:
        return sum(not r.success for r in self.records)

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records if r.success)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records if r.success)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def unpriced_calls(self) -> int:
        return sum(1 for r in self.records if r.success and r.cost_usd is None)

    @property
    def cost_usd(self) -> float | None:
        """Summed estimate; ``None`` if any successful call had an unknown price."""
        if self.unpriced_calls:
            return None
        return round(sum(r.cost_usd or 0.0 for r in self.records if r.success), 6)

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.model for r in self.records))

    @property
    def agents(self) -> tuple[str, ...]:
        return tuple(r.agent for r in self.records)

    @property
    def llm_latency_ms(self) -> float:
        return round(sum(r.latency_ms for r in self.records), 3)


_lock = threading.Lock()
_records: deque[UsageRecord] = deque(maxlen=_capacity_from_env())
_calls_total = 0
_file: IO[str] | None = None
_active_turns: ContextVar[tuple[TurnUsage, ...]] = ContextVar("careline_turn_usage", default=())


def _get_file() -> IO[str] | None:
    """Optional JSONL sink via CARELINE_USAGE_LOG (opened once, append mode)."""
    global _file
    if _file is None:
        path = os.environ.get("CARELINE_USAGE_LOG")
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _file = Path(path).open("a", encoding="utf-8")
    return _file


@contextmanager
def turn_scope() -> Iterator[TurnUsage]:
    """Collect the calls recorded inside this block (this context only).

    Nested scopes all receive the call. Calls made on *other* threads are not
    collected (``ContextVar`` isolation) — exactly what per-request cost needs.
    """
    turn = TurnUsage()
    token = _active_turns.set(_active_turns.get() + (turn,))
    try:
        yield turn
    finally:
        _active_turns.reset(token)


def record(
    *,
    agent: str,
    model: str,
    usage: object | None,
    latency_ms: float,
    success: bool = True,
) -> UsageRecord | None:
    """Record one call from a provider usage object (``input_tokens``/``output_tokens``
    on Anthropic; ``input_tokens``/``output_tokens`` or ``prompt_tokens``/
    ``completion_tokens`` on OpenAI). ``success=False`` marks a failed provider
    call (recorded for the failure count, excluded from cost/latency aggregates
    so one 60 s timeout cannot become the reported p99). Never raises.
    """
    global _calls_total
    try:
        it = int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0)
        ot = int(getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0)
        rec = UsageRecord(
            agent=agent,
            model=model,
            input_tokens=it,
            output_tokens=ot,
            latency_ms=round(latency_ms, 3),
            cost_usd=estimate_cost_usd(model, it, ot) if success else None,
            artifacts=dict(active_versions()),
            success=success,
        )
        with _lock:
            _records.append(rec)
            _calls_total += 1
            sink = _get_file()
            if sink is not None:
                sink.write(json.dumps(asdict(rec)) + "\n")
                sink.flush()
        for turn in _active_turns.get():
            turn.records.append(rec)
        return rec
    except Exception:  # observability must never break a clinical call
        return None


def records() -> tuple[UsageRecord, ...]:
    with _lock:
        return tuple(_records)


def set_capacity(capacity: int) -> None:
    """Resize the ring buffer (keeps the newest records). Test/ops hook."""
    global _records
    with _lock:
        _records = deque(_records, maxlen=max(1, int(capacity)))


def reset() -> None:
    """Test hook — clear the in-memory buffer and close the file sink."""
    global _file, _calls_total
    with _lock:
        _records.clear()
        _calls_total = 0
        if _file is not None:
            try:
                _file.close()
            except Exception:
                pass
        _file = None


def summary() -> dict:
    """Aggregate totals over the retained window — the cost-per-call number.

    Cost and latency statistics cover *successful* calls only; failures are
    counted separately (a 60 s timeout must not become the reported p99).
    ``per_call_usd`` divides by known-price successful calls only. For
    per-REQUEST cost use :func:`turn_scope` (a request is reasoner + verifier
    [+ judge] calls).
    """
    with _lock:
        recs = list(_records)
        total = _calls_total
        capacity = _records.maxlen
    if not recs:
        return {"calls": 0, "calls_total": total}
    ok = [r for r in recs if r.success]
    known = [r for r in ok if r.cost_usd is not None]
    lat = sorted(r.latency_ms for r in ok)
    pct = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else 0.0
    return {
        "calls": len(recs),
        "calls_total": total,
        "window_capacity": capacity,
        "failed_calls": len(recs) - len(ok),
        "total_tokens": sum(r.total_tokens for r in ok),
        "input_tokens": sum(r.input_tokens for r in ok),
        "output_tokens": sum(r.output_tokens for r in ok),
        "cost_usd": round(sum(r.cost_usd for r in known), 6) if known else None,
        "calls_with_unknown_price": len(ok) - len(known),
        "per_call_usd": (
            round(sum(r.cost_usd for r in known) / len(known), 6) if known else None
        ),
        "latency_ms_p50": pct(0.50),
        "latency_ms_p99": pct(0.99),
        "by_agent": {
            agent: sum(1 for r in recs if r.agent == agent)
            for agent in sorted({r.agent for r in recs})
        },
        "price_table_as_of": PRICE_TABLE_AS_OF,
    }


__all__ = [
    "DEFAULT_CAPACITY",
    "PRICE_TABLE_AS_OF",
    "PRICE_TABLE_USD_PER_MTOK",
    "TurnUsage",
    "UsageRecord",
    "estimate_cost_usd",
    "record",
    "records",
    "reset",
    "set_capacity",
    "summary",
    "turn_scope",
]
