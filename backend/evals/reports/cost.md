# Cost — per call vs per request

*Generated:* 2026-10-08T18:38:19+05:30 with `python -m scripts.cost_report --markdown evals/reports/cost.md` (active policy red_flags@v7). No live usage log exists (no live LLM run has been recorded), so **every figure below is an ESTIMATE**, not a measurement.

## ESTIMATE (chars/4 tokens, fixed output sizes, eval in-scope questions)

| Agent (per CALL) | Model | Est. input / output tokens | Est. $ per call |
|---|---|---|---|
| reasoner | gpt-4o-mini | 833 / 120 | $0.000197 |
| verifier | gpt-4o-mini | 353 / 80 | $0.000101 |
| judge | gpt-4o-mini | 490 / 60 | $0.000110 |

| Request type (per REQUEST = one patient question) | Calls | Est. $ |
|---|---|---|
| Red-flag escalation | none (rail fires before any LLM) | $0 |
| Declined / redirected | reasoner | $0.000197 |
| Answered | reasoner + verifier | $0.000298 |
| Answered, online judge at 20% | reasoner + verifier + 20% × judge | $0.000320 |

*$20 budget ÷ $0.000320 (most expensive request type, ESTIMATE) ≈ 62,500 requests.*
*Price table as of 2026-10 (USD/1M tokens), versioned in `careline/adapters/llm/usage.py`; unknown models are never guessed.*
