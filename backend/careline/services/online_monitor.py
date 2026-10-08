"""Online monitor — evaluation and monitoring of live turns, in process.

Fed one :func:`record` per QuestionService turn. Five categories, all over
bounded ring buffers (``CARELINE_MONITOR_WINDOW`` turns, default 1000):

(a) **operational** — latency p50/p95/p99, error rate (pipeline raised),
    fail-closed rate (reasoner/verifier unavailable → escalated), throughput.
(b) **output** — verdict mix, escalation rate, and a *low-risk escalation*
    proxy for over-escalation: escalations with no safety signal (not a red
    flag / cross-condition tripwire, not fail-closed, risk < 0.5). It is a
    proxy — without labels the monitor cannot know a turn was benign — and
    it is labelled as one.
(c) **quality / online evaluation** — samples ``CARELINE_JUDGE_SAMPLE_RATE``
    (default 0.2) of ANSWER turns and scores them with the LLM-as-judge on a
    background thread, against ONLY the cited facts valid at that turn. With
    no key this is the deterministic keyless judge twin; with
    ``OPENAI_API_KEY`` the real judge (prompt ``judge@v1``). Judge errors are
    counted, never scored faithful; a full queue drops (counted), never blocks.
    The judge runs inside a copy of the sampled turn's context
    (``contextvars.copy_context``), so its usage lands in THAT turn's usage
    scope and therefore in the turn's per-request cost below.
(d) **input drift** — the live question distribution vs the eval-set
    reference: PSI over the scope-category mix, out-of-vocabulary token rate
    vs the eval vocabulary (baseline = held-out cases vs dev vocabulary), and
    mean-length shift. Flagged when PSI > ``CARELINE_DRIFT_PSI`` (0.2), OOV
    rate exceeds the baseline by > 0.15, or length shifts > 50 %, once at
    least 30 turns are in the window. The reference (a keyless run of the
    whole eval set) is built OFF the request path: the API lifespan calls
    :meth:`OnlineMonitor.warm_reference` on a background thread at startup.
    Until it is ready drift reports ``reference_not_ready``; a failed build is
    cached and logged once (``reference_unavailable``) — never retried per
    request.
(e) **cost** — tokens and estimated $ per request from the usage turn scope
    (reasoner + verifier [+ the sampled judge] calls of that one turn; the
    judge's share appears once it has run).

Ops knobs (``CARELINE_MONITOR_WINDOW``, ``CARELINE_JUDGE_SAMPLE_RATE``,
``CARELINE_DRIFT_PSI``) are parsed defensively: a malformed or out-of-range
value falls back to the default with one warning, never a crash.

PHI stance: the monitor never stores question text — only aggregate features
(token count, OOV count, scope). Answer + cited-fact text are held only in the
bounded judge queue until judged, then discarded. Patient ids are not stored.

Monitoring must never break a clinical call: every entry point swallows its
own errors.

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import contextvars
import logging
import math
import os
import queue
import random
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from careline.domain.enums import ScopeCategory, TraceStatus, Verdict

DEFAULT_WINDOW = 1000
DEFAULT_JUDGE_SAMPLE_RATE = 0.2
DRIFT_MIN_SAMPLES = 30
DRIFT_PSI_THRESHOLD = 0.2
DRIFT_OOV_DELTA = 0.15
DRIFT_LENGTH_SHIFT = 0.5
_PSI_EPS = 1e-4
_SAFETY_SCOPES = frozenset({ScopeCategory.RED_FLAG, ScopeCategory.CROSS_CONDITION})
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_log = logging.getLogger(__name__)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _env_float(
    name: str, default: float, *, lo: float | None = None, hi: float | None = None
) -> float:
    """Float env knob; malformed / non-finite / out-of-range -> default + warning."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or (lo is not None and value < lo) or (
        hi is not None and value > hi
    ):
        _log.warning("%s=%r is invalid; using the default %s", name, raw, default)
        return default
    return value


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Int env knob; malformed or below ``minimum`` -> default + warning."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = minimum - 1
    if value < minimum:
        _log.warning("%s=%r is invalid; using the default %s", name, raw, default)
        return default
    return value


def _pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


# ---------------------------------------------------------------------------
# Drift reference (from the eval set)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftReference:
    scope_mix: dict[str, float]  # scope value -> share
    vocab: frozenset[str]
    oov_rate: float              # baseline OOV share (held-out vs dev vocab)
    mean_tokens: float
    n: int


@lru_cache(maxsize=1)
def build_reference() -> DriftReference:
    """Reference distribution from the committed eval set (keyless, deterministic).

    Scope mix = the keyless Brain's decisions over every eval question. The
    OOV baseline is the held-out questions' OOV rate against the dev
    (non-held-out) vocabulary — i.e. how "new" in-distribution questions
    normally look — and live OOV is measured against that same dev vocabulary.
    """
    # Lazy imports: only paid when drift is first computed.
    from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
    from careline.domain.brain.brain import Brain
    from careline.domain.model.call_session import CallSession
    from careline.services import eval_gate

    patients, now = eval_gate._load_seed()
    cases = eval_gate.load_cases()
    brain = Brain(reasoner=HeuristicReasoner(), verifier=HeuristicVerifier())
    scopes: Counter = Counter()
    for case in cases:
        decision = brain.run_question(
            question=case["question"],
            patient=patients[case["patient"]],
            now=now,
            session=CallSession(
                call_id=f"ref-{case['id']}", patient_id=case["patient"],
                doctor_id="dr-asha", max_clarify_turns=2,
            ),
        )
        scopes[_scope_key(decision.scope)] += 1
    total = sum(scopes.values())
    dev = [c for c in cases if not c["held_out"]]
    held = [c for c in cases if c["held_out"]]
    vocab = frozenset(t for c in dev for t in _tokens(c["question"]))
    held_tokens = [t for c in held for t in _tokens(c["question"])]
    oov = (sum(t not in vocab for t in held_tokens) / len(held_tokens)) if held_tokens else 0.0
    lengths = [len(_tokens(c["question"])) for c in cases]
    return DriftReference(
        scope_mix={k: v / total for k, v in scopes.items()},
        vocab=vocab,
        oov_rate=oov,
        mean_tokens=sum(lengths) / len(lengths),
        n=len(cases),
    )


def _scope_key(scope: ScopeCategory | None) -> str:
    return scope.value if isinstance(scope, ScopeCategory) else "unscoped"


def _psi(live: dict[str, float], ref: dict[str, float]) -> float:
    keys = set(live) | set(ref)
    total = 0.0
    for k in keys:
        p = max(live.get(k, 0.0), _PSI_EPS)
        q = max(ref.get(k, 0.0), _PSI_EPS)
        total += (p - q) * math.log(p / q)
    return total


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Turn:
    ts: float
    latency_ms: float
    verdict: str | None        # None = pipeline error
    scope: str
    fail_closed: bool
    low_risk_escalation: bool
    n_tokens: int
    n_oov: int | None          # None = drift reference was not ready yet
    llm_calls: int
    tokens: int
    cost_usd: float | None
    usage: Any = None          # the turn's live TurnUsage (judge spend lands later)

    def cost(self) -> tuple[int, int, float | None]:
        """(llm calls, tokens, $) — read live from the turn's usage scope."""
        if self.usage is not None:
            u = self.usage
            return u.calls, u.total_tokens, u.cost_usd
        return self.llm_calls, self.tokens, self.cost_usd


@dataclass(frozen=True)
class _Judged:
    faithful: bool
    score: float


def _is_fail_closed(decision) -> bool:
    for step in decision.trace.steps:
        if step.status is TraceStatus.TERMINAL and step.name in ("reasoner", "verifier"):
            return True
    return False


def _default_judge():
    from careline.adapters.llm.judge import build_judge

    try:
        return build_judge()
    except Exception:  # registry/SDK trouble: fall back to the deterministic twin
        from careline.adapters.llm.judge import KeylessJudge

        return KeylessJudge()


class OnlineMonitor:
    """Thread-safe, bounded, PHI-safe online monitor."""

    def __init__(
        self,
        *,
        window: int | None = None,
        judge: Any | None = None,
        judge_sample_rate: float | None = None,
        reference: DriftReference | None = None,
        seed: int | None = None,
        judge_queue_size: int = 100,
    ) -> None:
        self._window = (
            int(window)
            if window is not None and int(window) > 0
            else _env_int("CARELINE_MONITOR_WINDOW", DEFAULT_WINDOW)
        )
        rate = (
            judge_sample_rate
            if judge_sample_rate is not None
            else _env_float(
                "CARELINE_JUDGE_SAMPLE_RATE", DEFAULT_JUDGE_SAMPLE_RATE, lo=0.0, hi=1.0
            )
        )
        self._sample_rate = min(1.0, max(0.0, rate))
        self._judge = judge
        self._reference = reference
        self._reference_error: str | None = None
        self._reference_lock = threading.Lock()
        self._reference_started = reference is not None
        self._psi_threshold = _env_float("CARELINE_DRIFT_PSI", DRIFT_PSI_THRESHOLD, lo=0.0)
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._turns: deque[_Turn] = deque(maxlen=self._window)
        self._judged: deque[_Judged] = deque(maxlen=self._window)
        self._requests_total = 0
        self._sampled = 0
        self._judge_errors = 0
        self._dropped = 0
        self._started = time.time()
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, judge_queue_size))
        self._worker: threading.Thread | None = None
        self._closed = False

    # -- ingestion ---------------------------------------------------------

    def record(
        self,
        *,
        decision,
        question: str,
        latency_ms: float,
        cost: Any = None,
        patient=None,
        now: datetime | None = None,
    ) -> None:
        """Record one completed turn. ``cost`` is a usage ``TurnUsage`` or a float."""
        try:
            tokens = _tokens(question)
            ref = self._get_reference()  # never builds inline (see warm_reference)
            n_oov = sum(t not in ref.vocab for t in tokens) if ref is not None else None
            fail_closed = _is_fail_closed(decision)
            verdict = decision.verdict
            low_risk = (
                verdict is Verdict.ESCALATE
                and not fail_closed
                and decision.scope not in _SAFETY_SCOPES
                and decision.risk < 0.5
            )
            live_usage = None
            if isinstance(cost, (int, float)):
                llm_calls, tok, usd = 0, 0, float(cost)
            elif cost is not None:
                llm_calls, tok, usd = cost.calls, cost.total_tokens, cost.cost_usd
                live_usage = cost  # read live: a sampled judge's spend joins later
            else:
                llm_calls, tok, usd = 0, 0, None
            turn = _Turn(
                ts=time.time(),
                latency_ms=float(latency_ms),
                verdict=verdict.value,
                scope=_scope_key(decision.scope),
                fail_closed=fail_closed,
                low_risk_escalation=low_risk,
                n_tokens=len(tokens),
                n_oov=n_oov,
                llm_calls=llm_calls,
                tokens=tok,
                cost_usd=usd,
                usage=live_usage,
            )
            with self._lock:
                self._turns.append(turn)
                self._requests_total += 1
            if verdict is Verdict.ANSWER and patient is not None:
                self._maybe_judge(decision, patient, now)
        except Exception:
            return  # monitoring never breaks a clinical call

    def record_error(self, *, latency_ms: float) -> None:
        """Record a turn whose pipeline raised (no decision)."""
        try:
            with self._lock:
                self._turns.append(
                    _Turn(time.time(), float(latency_ms), None, "unscoped", False, False,
                          0, 0, 0, 0, None)
                )
                self._requests_total += 1
        except Exception:
            return

    # -- online evaluation ------------------------------------------------

    def _maybe_judge(self, decision, patient, now) -> None:
        if self._sample_rate <= 0.0 or self._rng.random() >= self._sample_rate:
            return
        now = now or datetime.now(timezone.utc)
        valid = {f.id: f.summary for f in patient.valid_slice(now).facts}
        cited = list(decision.citations)
        with self._lock:
            self._sampled += 1
        if not cited or any(fid not in valid for fid in cited):
            # Citing nothing, or a fact not valid NOW, is unfaithful by definition.
            with self._lock:
                self._judged.append(_Judged(False, 0.0))
            return
        # The judge runs in a COPY of this turn's context, so the judge's usage
        # record joins this turn's usage scope (per-request cost includes it).
        item = (
            contextvars.copy_context(),
            decision.answer_text or "",
            [(fid, valid[fid]) for fid in cited],
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_judge, name="careline-online-judge", daemon=True
            )
            self._worker.start()

    def _run_judge(self) -> None:
        while not self._closed:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                self._queue.task_done()
                break
            ctx, answer, facts = item
            try:
                if self._judge is None:
                    self._judge = _default_judge()
                verdict = ctx.run(self._judge.judge, answer=answer, facts=facts)
                with self._lock:
                    self._judged.append(_Judged(bool(verdict.faithful), float(verdict.score)))
            except Exception:
                with self._lock:
                    self._judge_errors += 1
            finally:
                del item, ctx, answer, facts  # PHI: nothing outlives the judgement
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued judgements finish (tests / graceful shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    # -- reporting ----------------------------------------------------------

    def _get_reference(self) -> DriftReference | None:
        """The drift reference if it is ready — NEVER built on the caller's thread."""
        return self._reference

    def warm_reference(self, *, background: bool = False) -> threading.Thread | None:
        """Build the drift reference once (the API lifespan calls this at startup).

        ``background=True`` builds on a daemon thread and returns it. A failed
        build is cached and logged ONCE — later calls are no-ops, so a broken
        eval set can never become a per-request retry storm. Never raises.
        """
        with self._reference_lock:
            if self._reference_started:
                return None
            self._reference_started = True
        if not background:
            self._build_reference()
            return None
        thread = threading.Thread(
            target=self._build_reference, name="careline-drift-reference", daemon=True
        )
        thread.start()
        return thread

    def _build_reference(self) -> None:
        try:
            self._reference = build_reference()
        except Exception as exc:
            self._reference_error = f"{type(exc).__name__}: {exc}"
            _log.warning(
                "online monitor: drift reference build failed (%s); input drift is "
                "disabled for this process (not retried)", self._reference_error,
            )

    def _judge_stamp(self) -> str:
        if self._judge is None:
            return "judge (lazy: keyless twin unless OPENAI_API_KEY is set)"
        return getattr(self._judge, "stamp", type(self._judge).__name__)

    def snapshot(self) -> dict:
        with self._lock:
            turns = list(self._turns)
            judged = list(self._judged)
            total = self._requests_total
            sampled, errors, dropped = self._sampled, self._judge_errors, self._dropped
        pending = self._queue.unfinished_tasks
        n = len(turns)
        decided = [t for t in turns if t.verdict is not None]
        now = time.time()

        # (a) operational
        lat = sorted(t.latency_ms for t in turns)
        span = (turns[-1].ts - turns[0].ts) if n > 1 else 0.0
        operational = {
            "requests_total": total,
            "window_size": n,
            "window_capacity": self._window,
            "latency_ms_p50": round(_pct(lat, 0.50), 3),
            "latency_ms_p95": round(_pct(lat, 0.95), 3),
            "latency_ms_p99": round(_pct(lat, 0.99), 3),
            "errors": sum(t.verdict is None for t in turns),
            "error_rate": (sum(t.verdict is None for t in turns) / n) if n else 0.0,
            "fail_closed": sum(t.fail_closed for t in turns),
            "fail_closed_rate": (sum(t.fail_closed for t in turns) / n) if n else 0.0,
            "throughput_rpm_1m": float(sum(1 for t in turns if now - t.ts <= 60.0)),
            "throughput_rps_window": round(n / span, 3) if span > 0 else None,
            "uptime_s": round(now - self._started, 1),
        }

        # (b) output
        verdicts = Counter(t.verdict for t in decided)
        nd = len(decided)
        output = {
            "verdict_counts": {v.value: verdicts.get(v.value, 0) for v in Verdict},
            "verdict_rates": {
                v.value: (verdicts.get(v.value, 0) / nd if nd else 0.0) for v in Verdict
            },
            "escalation_rate": (verdicts.get("escalate", 0) / nd) if nd else 0.0,
            "low_risk_escalation_rate": (
                sum(t.low_risk_escalation for t in decided) / nd if nd else 0.0
            ),
            "low_risk_escalation_note": (
                "over-escalation PROXY: escalations with no red-flag/cross-condition "
                "signal, not fail-closed, risk < 0.5"
            ),
            "scope_counts": dict(Counter(t.scope for t in decided)),
        }

        # (c) quality / online evaluation
        n_judged = len(judged)
        faithful = sum(j.faithful for j in judged)
        quality = {
            "judge": self._judge_stamp(),
            "sample_rate": self._sample_rate,
            "sampled": sampled,
            "judged": n_judged,
            "faithful": faithful,
            "faithfulness_rate": (faithful / n_judged) if n_judged else None,
            "mean_score": round(sum(j.score for j in judged) / n_judged, 4) if n_judged else None,
            "judge_errors": errors,
            "dropped": dropped,
            "pending": pending,
        }

        # (d) input drift
        drift = self._drift(decided)

        # (e) cost — read live, so a sampled judge's spend is in its turn
        costs = [t.cost() for t in turns]
        priced = [usd for _calls, _tok, usd in costs if usd is not None]
        total_cost = sum(priced)
        total_tokens = sum(tok for _calls, tok, _usd in costs)
        cost = {
            "basis": "estimate (provider token counts × versioned price table)",
            "includes": "reasoner + verifier + the sampled async judge, per turn",
            "requests_with_llm_calls": sum(calls > 0 for calls, _tok, _usd in costs),
            "total_tokens": total_tokens,
            "mean_tokens_per_request": (total_tokens / n) if n else 0.0,
            "total_cost_usd": round(total_cost, 6),
            "mean_cost_usd_per_request": (total_cost / len(priced)) if priced else None,
            "p95_cost_usd_per_request": (
                round(_pct(sorted(priced), 0.95), 6) if priced else None
            ),
            "requests_unknown_cost": n - len(priced),
        }

        alerts = []
        if n and operational["fail_closed_rate"] > 0.05:
            alerts.append(f"fail-closed rate {operational['fail_closed_rate']:.1%} > 5%")
        if n and operational["error_rate"] > 0.01:
            alerts.append(f"error rate {operational['error_rate']:.1%} > 1%")
        if n_judged >= 10 and quality["faithfulness_rate"] < 0.90:
            alerts.append(f"online faithfulness {quality['faithfulness_rate']:.1%} < 90%")
        if drift.get("drifted"):
            alerts.append("input drift: " + ", ".join(drift["reasons"]))

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operational": operational,
            "output": output,
            "quality": quality,
            "drift": drift,
            "cost": cost,
            "alerts": alerts,
        }

    def _drift(self, decided: list[_Turn]) -> dict:
        ref = self._get_reference()
        if ref is None:
            if self._reference_error is not None:
                return {"status": "reference_unavailable", "drifted": False, "reasons": [],
                        "error": self._reference_error}
            return {"status": "reference_not_ready", "drifted": False, "reasons": []}
        n = len(decided)
        if n < DRIFT_MIN_SAMPLES:
            return {
                "status": "insufficient_data",
                "drifted": False,
                "reasons": [],
                "samples": n,
                "min_samples": DRIFT_MIN_SAMPLES,
            }
        mix = Counter(t.scope for t in decided)
        live_mix = {k: v / n for k, v in mix.items()}
        psi = _psi(live_mix, ref.scope_mix)
        tokens = sum(t.n_tokens for t in decided)
        # OOV only over turns recorded after the reference was ready.
        with_oov = [t for t in decided if t.n_oov is not None]
        oov_tokens = sum(t.n_tokens for t in with_oov)
        oov = (sum(t.n_oov for t in with_oov) / oov_tokens) if oov_tokens else 0.0
        mean_len = tokens / n
        shift = abs(mean_len - ref.mean_tokens) / ref.mean_tokens if ref.mean_tokens else 0.0
        reasons = []
        if psi > self._psi_threshold:
            reasons.append(f"scope-mix PSI {psi:.2f} > {self._psi_threshold}")
        if oov - ref.oov_rate > DRIFT_OOV_DELTA:
            reasons.append(f"OOV rate {oov:.2f} vs baseline {ref.oov_rate:.2f}")
        if shift > DRIFT_LENGTH_SHIFT:
            reasons.append(f"mean length {mean_len:.1f} vs {ref.mean_tokens:.1f} tokens")
        return {
            "status": "ok" if not reasons else "drift",
            "drifted": bool(reasons),
            "reasons": reasons,
            "samples": n,
            "scope_psi": round(psi, 4),
            "scope_mix": {k: round(v, 4) for k, v in sorted(live_mix.items())},
            "reference_scope_mix": {k: round(v, 4) for k, v in sorted(ref.scope_mix.items())},
            "oov_rate": round(oov, 4),
            "reference_oov_rate": round(ref.oov_rate, 4),
            "mean_tokens": round(mean_len, 2),
            "reference_mean_tokens": round(ref.mean_tokens, 2),
            "psi_threshold": self._psi_threshold,
        }


# ---------------------------------------------------------------------------
# Process-wide default (what QuestionService and GET /monitoring use)
# ---------------------------------------------------------------------------

_default: OnlineMonitor | None = None
_default_lock = threading.Lock()


def get_monitor() -> OnlineMonitor:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = OnlineMonitor()
    return _default


def reset_monitor(monitor: OnlineMonitor | None = None) -> None:
    """Swap the process-wide monitor (tests) — ``None`` rebuilds lazily."""
    global _default
    with _default_lock:
        if _default is not None and _default is not monitor:
            _default.close()
        _default = monitor


def record(
    *,
    decision,
    question: str,
    latency_ms: float,
    cost: Any = None,
    patient=None,
    now: datetime | None = None,
) -> None:
    """Record one QuestionService turn on the process-wide monitor. Never raises."""
    try:
        get_monitor().record(
            decision=decision, question=question, latency_ms=latency_ms, cost=cost,
            patient=patient, now=now,
        )
    except Exception:
        return


def record_error(*, latency_ms: float) -> None:
    try:
        get_monitor().record_error(latency_ms=latency_ms)
    except Exception:
        return


def snapshot() -> dict:
    return get_monitor().snapshot()


def warm_reference_async() -> None:
    """Start building the process-wide monitor's drift reference off-thread. Never raises."""
    try:
        get_monitor().warm_reference(background=True)
    except Exception:
        return


__all__ = [
    "DriftReference",
    "OnlineMonitor",
    "build_reference",
    "get_monitor",
    "record",
    "record_error",
    "reset_monitor",
    "snapshot",
    "warm_reference_async",
]
