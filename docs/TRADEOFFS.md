# Trade-offs — the decisions we will defend

Every entry has the shape *we chose X over Y because Z*, with the number that
justified it. Constraints that shaped all of them: ~$20 total LLM budget, a
fixed timeline, five fictional patients, and an overriding safety rule
(uncertainty → escalate; zero cross-patient leakage).

## 1. Offline eval gate over a live canary deploy
**Chose:** a 250-item offline eval set run as a blocking CI check on every PR.
**Over:** routing 10% of live traffic to the candidate and auto-promoting on
live metrics (handout #11).
**Because:** the metric that matters — emergency recall — is *unmeasurable* in
organic traffic: emergencies are rare by definition, and our traffic is a
fictional demo with near-zero volume. A canary would take weeks to see one
missed emergency; the red-team split sees 60 per run. The number: v2 moved
recall 0.033 → 1.000 with over-escalation unchanged at 7.1%
(`evals/reports/shadow-v1-vs-v2.md`). The canary's honest replacement is the
shadow comparison — same decision, measured offline, zero user exposure.

## 2. Deterministic keyless CI slice over LLM-in-CI
**Chose:** every PR runs the gate on the deterministic spine (heuristic twins,
temperature-free rails) with no secrets at all; the LLM slice runs only on
labelled pushes with cached keys.
**Over:** calling GPT-4o-mini in the blocking CI path.
**Because:** fork PRs get no secrets, and a merge-blocking check that flakes
with provider latency/outages gets disabled by annoyed humans — which is how
gates die. The deterministic slice is reproducible (same run, same result),
runs in ~2 s, and catches exactly the class of regression that shipped at
baseline-v0 (a rail/policy change silently dropping recall). Cost: $0.

## 3. Semantic classifier + versioned rules over fine-tuning
**Chose:** a curated danger-phrase library (~140 phrases, 21 NHS 111/WHO
concepts) scored by token coverage + character-trigram cosine — pure Python,
~0.9 ms p50 — layered after the literal regexes, shipped as gated policy v2.
**Over:** fine-tuning a small classifier (handout #5).
**Because:** no training data exists for *our* patients, the free-tier deploy
has 512 MB (no GPU, no 1.5B weights), and a fine-tune is unauditable next to a
YAML phrase list a reviewer can read. The library's false-positive discipline
is pinned in tests: 59 paraphrased emergencies caught, 12 benign near-misses
("when I feel breathless") not escalated.

## 4. Shadow comparison over auto-promote
**Chose:** a promote/don't-promote table produced by running the eval set
through both configs in-process (`scripts/shadow_compare.py`); a human merges.
**Over:** automated promotion on metric wins.
**Because:** the release under review changes *safety policy*; an auto-promoter
that optimises a single metric can trade a quiet over-escalation regression for
a recall win without anyone noticing. The shadow table makes both columns
visible side by side; the gate then enforces the invariant (no leak, no miss,
no regression) mechanically.

## 5. Fail-closed spine over best-effort answers
**Chose:** any missing dependency, unavailable model, or ungroundable question
resolves to ESCALATE/CLARIFY — never a guess; the scope gate now re-runs the
rails itself before any redirect.
**Over:** answering with reduced confidence.
**Because:** in this domain a wrong answer is a safety incident while a
needless escalation is a minor cost — and over-escalation is *measured*
(gate ≤ 15%) so the safe direction can't quietly become the spam direction
(the failure commit `7c8acf3` originally fixed).

## 6. Single-process budget guard over distributed rate limiting
**Chose:** in-memory per-IP window + UTC daily cap in the ASGI middleware,
documented single-process assumption.
**Over:** Redis-backed distributed limits.
**Because:** the deploy is one free-tier container; every extra moving part is
another outage mode for a demo. When the app outgrows one process, the guard
interface stays, the counters move — and the daily cap (the actual budget
guarantee) should then live at the provider key level anyway.

## Cut scope, and what replaced each
| Cut | Why | Replacement |
|---|---|---|
| Live auto-promote canary | unmeasurable on demo traffic (see #1) | shadow comparison + gate |
| Vector-DB corpus RAG | retrieval here is per-patient fact *validity*, not corpus search | citation-groundedness + leak metrics, stated explicitly |
| Fine-tuned emergency model | no data, no GPU, unauditable | semantic phrase policy v2 (#3) |
| vLLM self-hosting | budget/ops cost with no accuracy win at this scale | provider APIs at gpt-4o-mini/haiku prices |
| Drift alerts | no sustained live traffic to drift yet | gate-vs-baseline regression check each PR |
