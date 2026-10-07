"""Per-request token & cost capture — the "$/request" half of the numbers.

Every live LLM call the adapters make is recorded here: model, tokens,
latency, estimated cost at a versioned price table, the active prompt/policy
stamps, and the agent that made the call. ``scripts/cost_report.py`` and the
Langfuse tracer consume these records; the README's cost-per-request number
is their aggregate. No network, no key — recording never blocks or fails a
clinical call (a broken metric must not become a broken diagnosis).

Prices are USD per 1M tokens (input, output), as of 2026-10. Unknown models
record ``cost_usd=None`` rather than a guess — the report says "unknown
price" instead of inventing a number.

Owner: Srujan (scope ``llm``).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

from careline.adapters.llm.prompt_registry import active_versions

# (input, output) USD per 1M tokens. Update with the as-of date when prices change.
PRICE_TABLE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.50, 10.00),
    "gpt-4o": (2.50, 10.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (15.00, 75.00),
}
PRICE_TABLE_AS_OF = "2026-10"


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
    agent: str                  # "reasoner" | "verifier" | "extractor" | ...
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


_lock = threading.Lock()
_records: list[UsageRecord] = []
_file: IO[str] | None = None


def _get_file() -> IO[str] | None:
    """Optional JSONL sink via CARELINE_USAGE_LOG (opened once, append mode)."""
    global _file
    if _file is None:
        path = os.environ.get("CARELINE_USAGE_LOG")
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _file = Path(path).open("a", encoding="utf-8")
    return _file


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
            sink = _get_file()
            if sink is not None:
                sink.write(json.dumps(asdict(rec)) + "\n")
                sink.flush()
        return rec
    except Exception:  # observability must never break a clinical call
        return None


def records() -> tuple[UsageRecord, ...]:
    with _lock:
        return tuple(_records)


def reset() -> None:
    """Test hook — clear the in-memory buffer and close the file sink."""
    global _file
    with _lock:
        _records.clear()
        if _file is not None:
            try:
                _file.close()
            except Exception:
                pass
        _file = None


def summary() -> dict:
    """Aggregate totals — the cost-per-request number for reports.

    Cost and latency statistics cover *successful* calls only; failures are
    counted separately (a 60 s timeout must not become the reported p99).
    ``per_call_usd`` divides by known-price successful calls only.
    """
    with _lock:
        recs = list(_records)
    if not recs:
        return {"calls": 0}
    ok = [r for r in recs if r.success]
    known = [r for r in ok if r.cost_usd is not None]
    lat = sorted(r.latency_ms for r in ok)
    pct = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else 0.0
    return {
        "calls": len(recs),
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
    "UsageRecord",
    "PRICE_TABLE_USD_PER_MTOK",
    "PRICE_TABLE_AS_OF",
    "estimate_cost_usd",
    "record",
    "records",
    "reset",
    "summary",
]
