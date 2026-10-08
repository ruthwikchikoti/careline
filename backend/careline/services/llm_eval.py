"""LLM eval slice — the eval set through the Brain on a live model, judged.

The keyless gate (:mod:`careline.services.eval_gate`) proves the safety spine
with deterministic twins; this slice measures what the twins cannot: does the
*real* Reasoner + Verifier (OpenAI, ``gpt-4o-mini`` by default) answer the
in-scope questions correctly, and is every answer faithful to the facts it
cites? Faithfulness is scored by an LLM-as-judge
(:mod:`careline.adapters.llm.judge`, prompt ``judge@v1``) that sees ONLY the
answer and the cited facts' text.

Cost discipline (budget cap ~$20):

* **Response cache** — every reasoner/verifier/judge call is cached on disk
  (sqlite under ``backend/evals/.cache/``, git-ignored) keyed by
  ``sha256(kind, model, prompt/policy version stamps, patient_id, question,
  rendered prompt)``. Re-running with unchanged versions bills nothing;
  bumping a prompt or policy version misses and re-bills exactly that.
  Provider errors are never cached.
* **Sampling** — ``--splits``, ``--limit`` (max cases per split) and
  ``--judge-sample`` (deterministic share of ANSWER turns judged). A sampled
  run is labelled as such and skips the split-floor check — it is a smoke
  test, not a release gate.

Fail closed: a provider error makes the Brain escalate (never answer), is
counted in ``provider_errors`` and trips the gate; a judge error counts as
unfaithful; a run that judged nothing has no faithfulness number and fails.

Exit codes: 0 pass · 1 gate tripped · 2 skipped, ONLY because no
``OPENAI_API_KEY`` is configured (the reason is printed).

Usage::

    python -m careline.services.eval_gate --mode llm                       # full set
    python -m careline.services.eval_gate --mode llm --limit 10 --judge-sample 0.2
    python -m careline.services.llm_eval --splits in_scope --json out.json

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from careline.adapters.llm import usage as usage_recorder
from careline.adapters.llm.judge import (
    DEFAULT_JUDGE_MODEL,
    JudgeUnavailable,
    OpenAIJudge,
    load_judge_prompt,
)
from careline.adapters.llm.openai_backend import OpenAIReasoner, OpenAIVerifier
from careline.adapters.llm.prompt_registry import active_versions
from careline.domain.brain.brain import Brain
from careline.domain.enums import Verdict
from careline.domain.model.call_session import CallSession
from careline.services import eval_gate

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CACHE_PATH = eval_gate._BACKEND_ROOT / "evals" / ".cache" / "llm_responses.sqlite"

# LLM-slice gates on top of every keyless safety gate. ``None`` fails (a run
# that produced no measurement is not a pass).
LLM_GATES = {
    "in_scope_answer_accuracy": ("min", 0.85),
    "judge_faithfulness_rate": ("min", 0.90),
    "provider_errors": ("max", 0),
}


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------


_AS_OF_RE = re.compile(r"\(as of [^)]*\)")


def cache_key(
    *,
    kind: str,
    model: str,
    stamps: dict[str, str],
    patient_id: str,
    question: str,
    payload: str,
) -> str:
    """Stable sha256 over everything that changes a model's output."""
    blob = json.dumps(
        [kind, model, sorted(stamps.items()), patient_id, question, payload],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ResponseCache:
    """A tiny sqlite key→JSON store. Thread-safe; git-ignores its own folder."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ignore = self.path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, value TEXT)")
        self._db.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM responses WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


class _CachingResponses:
    def __init__(self, owner: "CachingOpenAIClient") -> None:
        self._owner = owner

    def parse(self, *, model, instructions, input, text_format, **kwargs):  # noqa: A002
        return self._owner._parse(
            model=model, instructions=instructions, user=input, text_format=text_format, **kwargs
        )


class CachingOpenAIClient:
    """Wraps an ``openai.OpenAI``-shaped client: cache, count, price.

    Drop-in for the adapters' ``client=`` injection, so the real
    ``OpenAIReasoner`` / ``OpenAIVerifier`` / ``OpenAIJudge`` code paths run
    unchanged. A cache hit returns zero usage (nothing billed).
    """

    def __init__(self, inner, *, cache: ResponseCache | None, stamps: dict[str, str]) -> None:
        self._inner = inner
        self._cache = cache
        self._stamps = dict(stamps)
        self.patient_id = ""
        self.question = ""
        self.provider_calls = 0
        self.provider_errors = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.unpriced_calls = 0
        self._lock = threading.Lock()
        self.responses = _CachingResponses(self)

    def set_case(self, *, patient_id: str, question: str) -> None:
        self.patient_id, self.question = patient_id, question

    def _parse(self, *, model, instructions, user, text_format, **kwargs):
        # The seed's reference "now" is wall-clock, so the rendered "(as of …)"
        # stamp differs every run. The *set* of valid facts it selects is still
        # in the payload verbatim, so masking the timestamp keeps the key exact
        # for everything that can change the model's output.
        stable_user = _AS_OF_RE.sub("(as of <now>)", user)
        key = cache_key(
            kind=text_format.__name__,
            model=model,
            stamps=self._stamps,
            patient_id=self.patient_id,
            question=self.question,
            payload=hashlib.sha256(instructions.encode("utf-8")).hexdigest() + "\n" + stable_user,
        )
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                with self._lock:
                    self.cache_hits += 1
                return SimpleNamespace(
                    output_parsed=text_format.model_validate(hit),
                    usage=SimpleNamespace(input_tokens=0, output_tokens=0),
                    cached=True,
                )
            with self._lock:
                self.cache_misses += 1
        with self._lock:
            self.provider_calls += 1
        try:
            response = self._inner.responses.parse(
                model=model, instructions=instructions, input=user, text_format=text_format,
                **kwargs,
            )
        except Exception:
            with self._lock:
                self.provider_errors += 1
            raise  # the adapter converts this into a fail-closed escalation
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            with self._lock:
                self.provider_errors += 1
            return response  # adapter raises on a missing parse; never cached
        usage = getattr(response, "usage", None)
        it = int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0)
        ot = int(getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0)
        cost = usage_recorder.estimate_cost_usd(model, it, ot)
        with self._lock:
            self.input_tokens += it
            self.output_tokens += ot
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.cost_usd += cost
        if self._cache is not None:
            self._cache.put(key, parsed.model_dump(mode="json"))
        return response


# ---------------------------------------------------------------------------
# Selection & sampling
# ---------------------------------------------------------------------------


def select_cases(
    cases: list[dict], *, splits: list[str] | None = None, limit: int | None = None
) -> list[dict]:
    """Filter to ``splits`` and keep at most ``limit`` cases per split (file order)."""
    wanted = set(splits) if splits else None
    taken: dict[str, int] = {}
    out = []
    for case in cases:
        if wanted is not None and case["split"] not in wanted:
            continue
        n = taken.get(case["split"], 0)
        if limit is not None and n >= limit:
            continue
        taken[case["split"]] = n + 1
        out.append(case)
    return out


def judge_sampled(case_id: str, rate: float) -> bool:
    """Deterministic sampling: the same cases are judged on every run (cache-friendly)."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


# ---------------------------------------------------------------------------
# Run + score
# ---------------------------------------------------------------------------


@dataclass
class JudgedCase:
    case_id: str
    faithful: bool
    score: float
    unsupported_claims: tuple[str, ...]
    error: str | None = None


@dataclass
class LLMRun:
    results: list[eval_gate.CaseResult]
    judged: list[JudgedCase] = field(default_factory=list)
    judge_errors: int = 0


def run_llm_eval(
    cases: list[dict],
    patients: dict,
    now: datetime,
    *,
    client: CachingOpenAIClient,
    model: str,
    judge: OpenAIJudge,
    judge_sample: float,
) -> LLMRun:
    brain = Brain(
        reasoner=OpenAIReasoner(model=model, client=client),
        verifier=OpenAIVerifier(model=model, client=client),
    )
    run = LLMRun(results=[])
    for case in cases:
        patient = patients[case["patient"]]
        client.set_case(patient_id=case["patient"], question=case["question"])
        session = CallSession(
            call_id=f"llm-eval-{case['id']}",
            patient_id=case["patient"],
            doctor_id="dr-asha",
            max_clarify_turns=2,
        )
        start = time.perf_counter()
        decision = brain.run_question(
            question=case["question"], patient=patient, now=now, session=session
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        result = eval_gate.CaseResult(
            case=case,
            verdict=decision.verdict,
            citations=list(decision.citations),
            answer_text=decision.answer_text,
            latency_ms=latency_ms,
        )
        run.results.append(result)

        if decision.verdict is not Verdict.ANSWER or not judge_sampled(case["id"], judge_sample):
            continue
        valid = {f.id: f.summary for f in patient.valid_slice(now).facts}
        stale = [fid for fid in decision.citations if fid not in valid]
        if stale:
            # Citing a fact that is not valid NOW (superseded / not this
            # patient's) is unfaithful by definition — no judge call needed.
            run.judged.append(
                JudgedCase(case["id"], False, 0.0, (f"cites non-valid facts {stale}",))
            )
            continue
        facts = [(fid, valid[fid]) for fid in decision.citations]
        try:
            verdict = judge.judge(answer=decision.answer_text or "", facts=facts)
        except JudgeUnavailable as exc:
            run.judge_errors += 1
            run.judged.append(JudgedCase(case["id"], False, 0.0, (), error=str(exc)))
            continue
        run.judged.append(
            JudgedCase(case["id"], verdict.faithful, verdict.score, verdict.unsupported_claims)
        )
    return run


def evaluate_llm_gates(metrics: dict) -> list[str]:
    failures = eval_gate.evaluate_gates(metrics)
    for name, (op, threshold) in LLM_GATES.items():
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name} = None (no measurement — fail closed; {op} {threshold})")
        elif op == "max" and value > threshold:
            failures.append(f"{name} = {value} (max {threshold})")
        elif op == "min" and value < threshold:
            failures.append(f"{name} = {value:.3f} (min {threshold})")
    return failures


def score_llm(run: LLMRun, client: CachingOpenAIClient) -> dict:
    metrics = eval_gate.score(run.results)
    judged = len(run.judged)
    faithful = sum(j.faithful for j in run.judged)
    metrics.update(
        {
            "answers": sum(r.verdict is Verdict.ANSWER for r in run.results),
            "judged": judged,
            "judge_faithful": faithful,
            "judge_errors": run.judge_errors,
            "judge_faithfulness_rate": (faithful / judged) if judged else None,
            "judge_mean_score": (
                round(sum(j.score for j in run.judged) / judged, 4) if judged else None
            ),
            "provider_calls": client.provider_calls,
            "provider_errors": client.provider_errors,
            "cache_hits": client.cache_hits,
            "cache_misses": client.cache_misses,
            "input_tokens": client.input_tokens,
            "output_tokens": client.output_tokens,
            "cost_usd": round(client.cost_usd, 6),
            "unpriced_calls": client.unpriced_calls,
        }
    )
    return metrics


def has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def add_llm_arguments(parser: argparse.ArgumentParser) -> None:
    """LLM-slice flags, shared with ``eval_gate --mode llm``."""
    parser.add_argument("--model", default=DEFAULT_MODEL, help="reasoner+verifier model")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="max cases per split")
    parser.add_argument(
        "--splits", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None, help="comma-separated split names",
    )
    parser.add_argument(
        "--judge-sample", type=float, default=0.2,
        help="deterministic share of ANSWER turns judged (0..1)",
    )
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--no-cache", action="store_true", help="always call the provider")


def markdown_report(metrics: dict, failures: list[str]) -> str:
    lines = [
        "# Eval gate report — LLM slice",
        "",
        f"*When:* {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"*Model:* {metrics['model']} · *judge:* {metrics['judge_model']} "
        f"(sample {metrics['judge_sample']:.0%} of answers)",
        "*Artifacts:* " + ", ".join(metrics["artifacts"].values()),
        f"*Eval set sha256:* `{metrics['eval_set_sha256']}`",
    ]
    if metrics["sampled"]:
        lines.append(
            f"*Sampled run* ({metrics['n']} cases) — a smoke test, NOT a release gate; "
            "split floors not applied."
        )
    lines += ["", "| Metric | Value | Gate |", "|---|---|---|"]
    gates = {**{k: v for k, v in eval_gate.GATES.items()}, **LLM_GATES}
    for name, (op, thr) in gates.items():
        mark = " **FAIL**" if any(f.startswith(name) for f in failures) else ""
        lines.append(f"| {name} | {eval_gate._fmt(metrics.get(name))} | {op} {thr}{mark} |")
    lines += [
        f"| judge_mean_score | {eval_gate._fmt(metrics.get('judge_mean_score'))} | report only |",
        f"| answers / judged / judge errors | {metrics['answers']} / {metrics['judged']} / "
        f"{metrics['judge_errors']} | report only |",
        f"| provider calls / cache hits / misses | {metrics['provider_calls']} / "
        f"{metrics['cache_hits']} / {metrics['cache_misses']} | report only |",
        f"| tokens in / out | {metrics['input_tokens']:,} / {metrics['output_tokens']:,} "
        "| report only |",
        f"| estimated cost this run (USD) | ${metrics['cost_usd']:.4f} | report only |",
        "",
    ]
    if failures:
        lines += ["## Gate verdict: BLOCKED ⛔", ""] + [f"- {f}" for f in failures]
    else:
        lines += ["## Gate verdict: PASS ✅"]
    return "\n".join(lines) + "\n"


def run_cli(args: argparse.Namespace, *, client=None) -> int:
    """Execute the LLM slice for parsed ``args``. ``client`` injects a fake/real SDK client."""
    if client is None and not has_api_key():
        print(
            "LLM slice SKIPPED (no OPENAI_API_KEY): set OPENAI_API_KEY to run the "
            "eval set through the live reasoner/verifier + judge. Exit 2 = skipped.",
            file=sys.stderr,
        )
        return 2
    if client is None:
        try:
            from openai import OpenAI  # lazy: optional dependency
        except ImportError:
            client = _MissingSDK()  # every call fails → counted, fail closed
        else:
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())

    patients, now = eval_gate._load_seed()
    all_cases = eval_gate.load_cases(heldout_only=getattr(args, "heldout_only", False))
    eval_gate._validate(all_cases, patients)
    cases = select_cases(all_cases, splits=args.splits, limit=args.limit)
    sampled = bool(args.splits or args.limit is not None or getattr(args, "heldout_only", False))

    stamps = {**active_versions(), "judge": load_judge_prompt().stamp}
    cache = None if args.no_cache else ResponseCache(args.cache_path)
    caching = CachingOpenAIClient(client, cache=cache, stamps=stamps)
    judge = OpenAIJudge(model=args.judge_model, client=caching)
    try:
        run = run_llm_eval(
            cases, patients, now, client=caching, model=args.model, judge=judge,
            judge_sample=args.judge_sample,
        )
    finally:
        if cache is not None:
            cache.close()

    metrics = score_llm(run, caching)
    digest = eval_gate.eval_set_digest()
    metrics.update(
        {
            "mode": "llm",
            "model": args.model,
            "judge_model": args.judge_model,
            "judge_sample": args.judge_sample,
            "sampled": sampled,
            "artifacts": stamps,
            "eval_set_sha256": digest["combined"],
            "eval_set_files_sha256": digest["files"],
            # Per-case outcomes: lets a later run be regression-checked on the
            # shared case ids only (sampled runs, grown sets) — see eval_gate.
            "per_case": eval_gate.per_case_results(run.results),
        }
    )
    failures = evaluate_llm_gates(metrics)
    if not sampled:
        failures += eval_gate.check_split_floors(metrics["splits"])
    if getattr(args, "baseline", None):
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        failures += eval_gate.regression_check(
            metrics, baseline, known_ids={c["id"] for c in eval_gate.load_cases()}
        )
    metrics["gate_failures"] = failures

    report = markdown_report(metrics, failures)
    if getattr(args, "json_out", None):
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if getattr(args, "md_out", None):
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_out).write_text(report, encoding="utf-8")
    print(report)
    if failures:
        print(f"LLM EVAL GATE BLOCKED — {len(failures)} gate violation(s).", file=sys.stderr)
        return 1
    print("LLM EVAL GATE PASSED")
    return 0


class _MissingSDK:
    class responses:  # noqa: N801 - SDK shape
        @staticmethod
        def parse(**_kwargs):
            raise RuntimeError("openai SDK is not installed (pip install -e '.[llm]')")


def main(argv: list[str] | None = None, *, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_llm_arguments(parser)
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--markdown", dest="md_out")
    parser.add_argument("--baseline")
    parser.add_argument("--heldout-only", action="store_true")
    return run_cli(parser.parse_args(argv), client=client)


__all__ = [
    "CachingOpenAIClient",
    "LLM_GATES",
    "ResponseCache",
    "add_llm_arguments",
    "cache_key",
    "evaluate_llm_gates",
    "judge_sampled",
    "main",
    "run_cli",
    "run_llm_eval",
    "score_llm",
    "select_cases",
]


if __name__ == "__main__":
    raise SystemExit(main())
