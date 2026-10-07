"""Cost report — per-request $ estimate for the README's numbers table.

Two inputs, whichever exists:

1. **Measured** — the JSONL written by ``CARELINE_USAGE_LOG`` (per-call tokens
   and cost captured by the live adapters). Always preferred when present.
2. **Estimated** — no key needed: renders the actual Reasoner + Verifier
   messages for the eval in-scope questions over the seeded patients, counts
   tokens as chars/4 (a standard planning approximation), and prices them from
   the versioned table in ``careline.adapters.llm.usage``. Clearly labelled as
   an estimate; swap in a live run to replace it with measured numbers.

    cd backend && python -m scripts.cost_report --markdown evals/reports/cost.md

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from careline.adapters.llm import prompts, usage
from careline.services.eval_gate import _load_seed, load_cases

_CHARS_PER_TOKEN = 4  # planning approximation; measured runs replace this


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_per_question(model: str) -> dict:
    """Reasoner + Verifier tokens/cost for each in-scope eval question."""
    patients, _now = _load_seed()
    cases = [c for c in load_cases() if c["split"] == "in_scope"]
    rows = []
    for case in cases:
        patient = patients[case["patient"]]
        slice_ = patient.valid_slice(_now)
        reasoner_msg = (
            prompts.REASONER_SYSTEM_PROMPT
            + prompts.build_reasoner_user_message(question=case["question"], context=slice_)
        )
        verifier_msg = (
            prompts.VERIFIER_SYSTEM_PROMPT
            + prompts.build_verifier_user_message(
                question=case["question"],
                candidate_answer="Your doctor advised following the approved plan.",
                citations=["med-1"],
                context=slice_,
            )
        )
        in_tok = _estimate_tokens(reasoner_msg) + _estimate_tokens(verifier_msg)
        # Structured outputs are short (scope, candidate, confidence, citations).
        out_tok = 120 + 80
        cost = usage.estimate_cost_usd(model, in_tok, out_tok)
        rows.append({"id": case["id"], "input_tokens": in_tok, "output_tokens": out_tok,
                     "cost_usd": cost})
    known = [r for r in rows if r["cost_usd"] is not None]
    return {
        "questions": len(rows),
        "mean_input_tokens": sum(r["input_tokens"] for r in rows) // len(rows),
        "mean_output_tokens": sum(r["output_tokens"] for r in rows) // len(rows),
        "mean_cost_usd": (
            sum(r["cost_usd"] for r in known) / len(rows) if known else None
        ),
        "model": model,
        "basis": "estimate (chars/4, eval in-scope questions, live-run to replace)",
    }


def measured_from_jsonl(path: str) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    records = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
    if not records:
        return None
    return {
        "basis": f"measured ({len(records)} calls from {path})",
        "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in records),
        "total_cost_usd": sum(r["cost_usd"] or 0 for r in records),
        "per_call_usd": sum(r["cost_usd"] or 0 for r in records) / len(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--markdown", help="write the report here")
    args = parser.parse_args()

    import os

    measured = measured_from_jsonl(os.environ.get("CARELINE_USAGE_LOG", "usage.jsonl"))
    est = estimate_per_question(args.model)

    lines = ["# Cost per request", ""]
    if measured:
        lines += [
            f"**Measured:** {measured['basis']} — {measured['total_tokens']:,} tokens, "
            f"${measured['total_cost_usd']:.4f} total, "
            f"**${measured['per_call_usd']:.6f}/call**.",
            "",
        ]
    lines += [
        f"**Estimated** ({est['basis']}), {est['model']}:",
        f"mean {est['mean_input_tokens']:,} input + {est['mean_output_tokens']} output "
        f"tokens per question (reasoner + verifier) → "
        f"**${est['mean_cost_usd']:.6f} per question**.",
        "",
        f"*Price table as of {usage.PRICE_TABLE_AS_OF} (USD/1M tokens), versioned in "
        "`careline/adapters/llm/usage.py`; unknown models are never guessed.*",
        f"*$20 budget ÷ ${est['mean_cost_usd']:.6f} ≈ "
        f"{int(20 / est['mean_cost_usd']):,} questions.*",
    ]
    report = "\n".join(lines) + "\n"
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
