"""Shadow comparison — the offline stand-in for a canary deploy.

Runs the same questions through two rail configurations *in the same process*
and prints a side-by-side metrics table, so a candidate release can be judged
on evidence before it merges. Chosen over a live canary because the traffic
here is fictional and low-volume, and emergency recall — the metric that
matters — is too rare in organic traffic for a canary to measure at all;
the red-team eval set measures it directly on every candidate.

Default comparison: red-flag policy v1 (literal regexes only) vs the current
active policy (v2 = regexes + semantic detector). Extend with --prompts once
the LLM slice wires prompt-variant runs.

    cd backend && python -m scripts.shadow_compare \
        --a-label "red_flags@v1 (regex only)" \
        --b-label "red_flags@v2 (+semantic)" \
        --markdown evals/reports/shadow-v1-vs-v2.md

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
from datetime import datetime

import careline.domain.brain.brain as brain_module
import careline.domain.gates.chain as chain_module
from careline.adapters.llm.prompt_registry import active_versions
from careline.domain.rails import red_flag
from careline.services.eval_gate import (
    _load_seed,
    load_cases,
    run_keyless,
    score,
)


def _regex_only(question: str) -> str | None:
    """The v1 rail: literal regexes, no semantic layer."""
    match = red_flag._RED_FLAG_RE.search(question)
    return match.group(0) if match else None


def run_variant(label: str, *, regex_only: bool) -> dict:
    # The rail is imported in TWO places (Brain's pre-LLM rail and the scope
    # gate's defense-in-depth re-check) — a faithful v1 shadow patches both.
    originals = (
        brain_module.check_red_flag,
        chain_module.check_red_flag,
    )
    try:
        if regex_only:
            brain_module.check_red_flag = _regex_only
            chain_module.check_red_flag = _regex_only
        patients, now = _load_seed()
        results = run_keyless(load_cases(), patients, now)
        metrics = score(results)
    finally:
        brain_module.check_red_flag, chain_module.check_red_flag = originals
    metrics["variant"] = label
    return metrics


_METRIC_ROWS = [
    ("Emergency recall", "emergency_recall", "higher_better", 3),
    ("Missed emergencies", "missed_emergencies", "lower_better", 0),
    ("Cross-patient leaks", "cross_patient_leaks", "lower_better", 0),
    ("Superseded leaks", "superseded_leaks", "lower_better", 0),
    ("Injection answered", "injection_answered", "lower_better", 0),
    ("Out-of-scope redirect acc.", "out_of_scope_redirect_accuracy", "higher_better", 3),
    ("No-answer accuracy", "no_answer_accuracy", "higher_better", 3),
    ("Over-escalation rate", "over_escalation_rate", "lower_better", 3),
    ("Latency p50 (ms)", "latency_ms_p50", "lower_better", 3),
    ("Latency p99 (ms)", "latency_ms_p99", "lower_better", 3),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-label", default="red_flags@v1 (regex only)")
    parser.add_argument("--b-label", default=f"active ({active_versions()['red_flags']})")
    parser.add_argument("--markdown", help="write the comparison table here")
    args = parser.parse_args()

    a = run_variant(args.a_label, regex_only=True)
    b = run_variant(args.b_label, regex_only=False)

    lines = [
        "# Shadow comparison — candidate release vs incumbent",
        "",
        f"*When:* {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"*Set:* the full 250-item eval set, keyless deterministic slice",
        "",
        "| Metric | A: " + args.a_label + " | B: " + args.b_label + " | Better |",
        "|---|---|---|---|",
    ]
    for title, key, direction, nd in _METRIC_ROWS:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            continue
        fa, fb = f"{va:.{nd}f}" if isinstance(va, float) else str(va), (
            f"{vb:.{nd}f}" if isinstance(vb, float) else str(vb)
        )
        if va == vb:
            better = "="
        elif (vb > va) if direction == "higher_better" else (vb < va):
            better = "**B**"
        else:
            better = "A"
        lines.append(f"| {title} | {fa} | {fb} | {better} |")
    lines += [
        "",
        f"*In-scope answer accuracy (informational, keyless): "
        f"A={a['in_scope_answer_accuracy_informational']:.3f} "
        f"B={b['in_scope_answer_accuracy_informational']:.3f}*",
        "",
        "Verdict: promote B iff every safety metric is B-or-equal and the",
        "eval gate passes on B — see evals/reports/ for the gate run.",
    ]
    table = "\n".join(lines) + "\n"
    if args.markdown:
        from pathlib import Path

        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(table, encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
