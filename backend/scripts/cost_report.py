"""Cost report — per-CALL vs per-REQUEST $, estimates labelled as estimates.

Two units, never conflated:

* **per CALL** — one provider call by one agent (reasoner, verifier, judge,
  extractor). This is what ``usage.summary()['per_call_usd']`` reports.
* **per REQUEST** — one patient question = the calls one turn makes:
  reasoner + verifier (the verifier runs only when the reasoner proposes an
  answer) + judge × the online sample rate (only ANSWER turns are judged). A
  red-flag escalation makes no LLM call at all ($0).

Two inputs, whichever exists:

1. **Measured** — the JSONL written by ``CARELINE_USAGE_LOG`` (per-call tokens
   and cost captured by the live adapters). Per-call numbers are measured
   per agent; the per-request number composed from them is DERIVED (the log
   has no request id) and labelled as such.
2. **ESTIMATE** — no key needed: renders the actual Reasoner, Verifier and
   Judge messages for the eval in-scope questions over the seeded patients,
   counts tokens as chars/4 (a planning approximation), assumes fixed
   structured-output sizes, and prices them from the versioned table in
   ``careline.adapters.llm.usage``. Every figure from this path is an
   ESTIMATE; swap in a live run to replace it with measured numbers.

    cd backend && python -m scripts.cost_report --markdown evals/reports/cost.md
    python -m scripts.cost_report --model gpt-4o-mini --judge-sample 0.2

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from careline.adapters.llm import prompts, usage
from careline.adapters.llm.judge import build_judge_user_message, load_judge_prompt
from careline.services.eval_gate import _load_seed, load_cases

_CHARS_PER_TOKEN = 4  # planning approximation; measured runs replace this
# Assumed structured-output sizes (tokens) — part of the ESTIMATE.
_OUT_TOKENS = {"reasoner": 120, "verifier": 80, "judge": 60}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate(model: str, judge_model: str, judge_sample: float) -> dict:
    """ESTIMATED per-call and per-request tokens/cost over the eval in-scope questions."""
    patients, now = _load_seed()
    cases = [c for c in load_cases() if c["split"] == "in_scope"]
    judge_prompt = load_judge_prompt().text
    per_agent: dict[str, list[int]] = {"reasoner": [], "verifier": [], "judge": []}
    for case in cases:
        slice_ = patients[case["patient"]].valid_slice(now)
        cites = [f for f in case["expected"].get("must_cite", []) if any(x.id == f for x in slice_.facts)]
        facts = [(f.id, f.summary) for f in slice_.facts if f.id in cites] or [
            (f.id, f.summary) for f in slice_.facts[:1]
        ]
        candidate = " ".join(text for _id, text in facts)
        per_agent["reasoner"].append(_estimate_tokens(
            prompts.REASONER_SYSTEM_PROMPT
            + prompts.build_reasoner_user_message(question=case["question"], context=slice_)
        ))
        per_agent["verifier"].append(_estimate_tokens(
            prompts.VERIFIER_SYSTEM_PROMPT
            + prompts.build_verifier_user_message(
                question=case["question"], candidate_answer=candidate,
                citations=[fid for fid, _ in facts], context=slice_,
            )
        ))
        per_agent["judge"].append(_estimate_tokens(
            judge_prompt + build_judge_user_message(answer=candidate, facts=facts)
        ))

    calls = {}
    for agent, ins in per_agent.items():
        mean_in = sum(ins) / len(ins)
        m = judge_model if agent == "judge" else model
        calls[agent] = {
            "model": m,
            "mean_input_tokens": round(mean_in),
            "output_tokens": _OUT_TOKENS[agent],
            "cost_usd": usage.estimate_cost_usd(m, round(mean_in), _OUT_TOKENS[agent]),
        }
    return {
        "basis": "ESTIMATE (chars/4 tokens, fixed output sizes, eval in-scope questions)",
        "questions": len(cases),
        "per_call": calls,
        "per_request": compose_request(calls, judge_sample),
    }


def compose_request(per_call: dict, judge_sample: float) -> dict:
    """Per-REQUEST cost from per-call costs: reasoner + verifier [+ judge × sample]."""
    def cost(agent):
        return (per_call.get(agent) or {}).get("cost_usd")

    r, v, j = cost("reasoner"), cost("verifier"), cost("judge")
    out = {
        "judge_sample_rate": judge_sample,
        "red_flag_turn_usd": 0.0,  # rail escalates before any LLM call
        "declined_turn_usd": r,    # reasoner only (no candidate → no verifier)
        "answer_turn_usd": (r + v) if r is not None and v is not None else None,
    }
    out["answer_turn_with_judge_usd"] = (
        out["answer_turn_usd"] + j * judge_sample
        if out["answer_turn_usd"] is not None and j is not None else None
    )
    return out


def measured_from_jsonl(path: str) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    records = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
    ok = [r for r in records if r.get("success", True)]
    if not ok:
        return None
    per_call = {}
    for agent in sorted({r["agent"] for r in ok}):
        rs = [r for r in ok if r["agent"] == agent]
        priced = [r for r in rs if r["cost_usd"] is not None]
        per_call[agent] = {
            "calls": len(rs),
            "model": ",".join(sorted({r["model"] for r in rs})),
            "mean_input_tokens": round(sum(r["input_tokens"] for r in rs) / len(rs)),
            "mean_output_tokens": round(sum(r["output_tokens"] for r in rs) / len(rs)),
            "cost_usd": (sum(r["cost_usd"] for r in priced) / len(priced)) if priced else None,
        }
    return {
        "basis": f"MEASURED per call ({len(ok)} successful calls from {path}); "
                 "per-request is DERIVED from per-call means",
        "failed_calls": len(records) - len(ok),
        "total_cost_usd": sum(r["cost_usd"] or 0 for r in ok),
        "per_call": per_call,
    }


def _usd(v) -> str:
    return "unknown price" if v is None else f"${v:.6f}"


def render(est: dict, measured: dict | None, judge_sample: float) -> str:
    lines = ["# Cost — per call vs per request", ""]
    if measured:
        req = compose_request(measured["per_call"], judge_sample)
        lines += [
            f"## Measured — {measured['basis']}",
            "",
            "| Agent (per CALL) | Calls | Model | Mean in / out tokens | $ per call |",
            "|---|---|---|---|---|",
        ]
        for agent, c in measured["per_call"].items():
            lines.append(
                f"| {agent} | {c['calls']} | {c['model']} | {c['mean_input_tokens']:,} / "
                f"{c['mean_output_tokens']} | {_usd(c['cost_usd'])} |"
            )
        lines += [
            "",
            f"Per REQUEST (DERIVED): answer turn {_usd(req['answer_turn_usd'])}; "
            f"with judge at {judge_sample:.0%} sampling {_usd(req['answer_turn_with_judge_usd'])}; "
            f"declined turn {_usd(req['declined_turn_usd'])}; red-flag turn $0.",
            "",
        ]
    lines += [
        f"## {est['basis']}",
        "",
        "| Agent (per CALL) | Model | Est. input / output tokens | Est. $ per call |",
        "|---|---|---|---|",
    ]
    for agent, c in est["per_call"].items():
        lines.append(
            f"| {agent} | {c['model']} | {c['mean_input_tokens']:,} / {c['output_tokens']} "
            f"| {_usd(c['cost_usd'])} |"
        )
    req = est["per_request"]
    lines += [
        "",
        "| Request type (per REQUEST = one patient question) | Calls | Est. $ |",
        "|---|---|---|",
        "| Red-flag escalation | none (rail fires before any LLM) | $0 |",
        f"| Declined / redirected | reasoner | {_usd(req['declined_turn_usd'])} |",
        f"| Answered | reasoner + verifier | {_usd(req['answer_turn_usd'])} |",
        f"| Answered, online judge at {judge_sample:.0%} | reasoner + verifier + "
        f"{judge_sample:.0%} × judge | {_usd(req['answer_turn_with_judge_usd'])} |",
        "",
    ]
    worst = req["answer_turn_with_judge_usd"]
    if worst:
        lines.append(
            f"*$20 budget ÷ {_usd(worst)} (most expensive request type, ESTIMATE) ≈ "
            f"{int(20 / worst):,} requests.*"
        )
    lines.append(
        f"*Price table as of {usage.PRICE_TABLE_AS_OF} (USD/1M tokens), versioned in "
        "`careline/adapters/llm/usage.py`; unknown models are never guessed.*"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4o-mini", help="reasoner + verifier model")
    parser.add_argument("--judge-model", default="gpt-4o-mini")
    parser.add_argument(
        "--judge-sample", type=float,
        default=float(os.environ.get("CARELINE_JUDGE_SAMPLE_RATE", 0.2)),
    )
    parser.add_argument("--usage-log", default=os.environ.get("CARELINE_USAGE_LOG", "usage.jsonl"))
    parser.add_argument("--markdown", help="write the report here")
    args = parser.parse_args(argv)

    measured = measured_from_jsonl(args.usage_log)
    est = estimate(args.model, args.judge_model, args.judge_sample)
    report = render(est, measured, args.judge_sample)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
