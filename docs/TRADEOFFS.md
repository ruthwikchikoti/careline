# Trade-offs — the decisions we will defend

Every entry has the same five parts:

1. the decision, written as **"We chose X over Y because Z"**;
2. the alternatives we rejected, and why;
3. the constraint that forced the decision;
4. the evidence, with the command or committed report it comes from;
5. the failure mode the decision creates, and how we mitigate it.

**Constraints behind all of them.** About $20 of LLM spend in total. A free-tier deploy (Render free web service: 512 MB RAM, no GPU, one container). No real patient data: five fictional patients and one doctor. A fixed timeline. And the safety rule that outranks everything else: *uncertainty resolves to ESCALATE, never answer from a superseded fact, zero cross-patient reachability.*

**Where the numbers come from.** Every number below can be reproduced from `backend/` with a keyless environment (`CARELINE_MONGO_URI= OPENAI_API_KEY= ...`). Gate figures are from `python -m careline.services.eval_gate` at the active policy `red_flags@v8+8dd13f40326f` (391 items, re-run 2026-10-08; `evals/reports/after-policy-v8.md`). Blind-battery figures are from `python -m scripts.score_blind evals/blind/battery-N.json`. Model-comparison costs are pre-run **estimates** from `python -m scripts.cost_report` (tokens counted as chars/4, priced from the versioned table in `careline/adapters/llm/usage.py`). Measured gpt-4o-mini numbers come from two live end-to-end flow checks on 2026-10-08 (`evals/reports/live-flow-gpt-4o-mini-run1.md` and `-run2.md`, 18 portal questions each: $0.00024 / $0.00022 per question, $0.00031 / $0.00029 per model-handled question, p95/max 3.6 s / 5.3 s). The full 391-item LLM slice has not been run.

| # | We chose | Over | Constraint that forced it |
|---|---|---|---|
| 1 | Offline eval gate + shadow comparison | Live canary | Data availability (emergencies are rare; demo traffic is fictional) |
| 2 | Deterministic keyless CI slice | LLM-in-CI | Cost ($20) and reproducibility; fork PRs get no secrets |
| 3 | Lexical + structural emergency rail | Fine-tuned classifier | No training data; 512 MB free tier; auditability |
| 4 | Fail-closed ESCALATE | Best-effort answer | Safety rule; the cost of a wrong answer |
| 5 | Two LLM calls (Reasoner + independent Verifier) | One self-checking call | Safety rule against hallucinated grounding |
| 6 | gpt-4o-mini | Stronger or pricier models | Cost ($20) |
| 7 | Shared-case regression vs a committed baseline | A moving baseline | Data growth (the set went 250 → 391) |
| 8 | Blind batteries | Self-written probes | Honest generalisation numbers |
| 9 | Render free tier | Fly.io / HF Spaces | Cost ($0), blueprint-as-code |
| 10 | In-process monitor (`GET /monitoring`) | Prometheus + Grafana | 512 MB, one container |
| 11 | Langfuse | LangSmith | PHI posture (self-hostable), open source |
| 12 | Per-doctor hashed credentials | SSO / OIDC | Demo scope, no IdP |
| 13 | Shadow comparison + human merge | Automatic promotion | Safety policy is multi-metric |
| 14 | Single-process budget guard | Redis-backed distributed limits | One free-tier container |

---

## 1. Offline eval gate over a live canary deploy

**We chose** an offline eval gate, run on every PR against 391 labelled items and designed to block the merge (branch protection is still pending, see #7), plus an offline shadow comparison of candidate and incumbent policy, **over** a live canary that routes a slice of real traffic to the candidate and promotes on live metrics, **because** the metric that matters, emergency recall, cannot be measured on organic traffic. Emergencies are rare by definition, and our traffic is a fictional demo with near-zero volume. A canary would need weeks to see a single missed emergency. The emergency split puts 160 in front of every candidate; the whole gate takes about 1.7 s.

- **Alternatives rejected.**
  - *Canary at 10% with auto-rollback.* There is no traffic to split, and a missed emergency would be discovered by harming a patient.
  - *A/B test on user feedback.* Patients cannot judge whether an escalation was clinically right.
- **Constraint.** Data availability: there are no real calls and no labelled production traffic.
- **Evidence.**
  - `python -m scripts.shadow_compare --a v1 --b v8 --case-ids evals/reports/baseline-v0.case_ids.txt` scores the frozen 250 original items. Emergency recall goes 0.033 → 1.000 (58 → 0 missed). Over-escalation goes 0.083 → 0.094: the cost column a canary would also have to show.
- **Failure mode it creates.** The gate measures our own eval distribution, not live users. If live wording drifts away from the eval set, the gate stays green while recall drops.
- **Mitigation.**
  - Blind batteries (#8) measure recall on wording nobody tuned against. The current number is 45/50 (battery 3, blind to v6, v7 and v8; 44/50 at v6 and v7).
  - The online monitor (`GET /monitoring`) computes input drift against the eval set: PSI over the scope mix, OOV rate and length shift. It raises an alert at PSI > 0.2.
  - Any fresh miss becomes an eval item before the next release.

## 2. Deterministic keyless CI slice over LLM-in-CI

**We chose** to make the merge-blocking check a deterministic slice with no secrets at all: the full Brain, offline heuristic Reasoner and Verifier twins, and temperature-free rails. **We chose this over** calling GPT-4o-mini in the blocking path, **because** a merge-blocking check has to be reproducible (same commit, same verdict), free, and available to fork PRs, which get no secrets. A check that flakes when the provider is slow or down gets switched off by annoyed humans, and that is how gates die.

- **Alternatives rejected.**
  - *LLM-in-CI on every PR.* About $0.0003 × 391 items × every push, plus provider nondeterminism and outages, would make the check unreliable.
  - *No CI eval, manual testing only.* That is exactly how baseline-v0 shipped with 58/60 emergencies missed.
- **Constraint.** Cost: $20 in total. Reproducibility. Fork PRs carry no secrets.
- **Evidence.**
  - The gate runs in about 1.7 s wall on a laptop (`time python -m careline.services.eval_gate`). Per-case latency is p50 ≈ 4.0 ms / p99 ≈ 11 ms. Cost: $0.
  - Replaying the tagged commit that produced baseline-v0, `python -m scripts.shadow_compare --replay baseline-v0`, exits with gate code 1: 58/60 missed.
- **Failure mode it creates.** The keyless twins cannot paraphrase, so the keyless slice cannot measure answer quality. Keyless in-scope answer accuracy is **0.167** (0.176 on the 339 cases shared with v5), and a change to a *prompt* (`prompts/reasoner/v1.md`) is not exercised by the twins.
- **Mitigation.**
  - In-scope accuracy is a regression-only gate: it must not drop against the baseline.
  - Prompt files are hash-stamped in `prompts/manifest.yaml`, and a mismatch fails at import, so a prompt change is always visible.
  - The LLM slice (`eval_gate --mode llm`: live Reasoner + Verifier + LLM-as-judge, gates in-scope accuracy ≥ 0.85 and judge faithfulness ≥ 0.90, on-disk response cache) is implemented, but **the full 391-item slice has not been run**. One live end-to-end flow check (18 questions) has run, and it caught a prompt bug the keyless slice could not: extractor v1 recorded "instead of 1000mg" as a second current fact (fixed in extractor v2). In CI the slice runs on pushes to our own repo with `continue-on-error`; with no `OPENAI_API_KEY` secret it exits 0, so the job shows **green**, and it writes SKIPPED to the job summary plus a warning annotation.
  - We say plainly that prompt and model changes are not gated until that slice runs.

## 3. Lexical + structural emergency rail over a fine-tuned classifier

**We chose** a deterministic, versioned emergency rail. It has several layers:

- literal patterns;
- since v6, de-obfuscation (letter-, hyphen- and dot-spaced words, leetspeak inside words) and a generic distress / help-seeking family;
- a curated danger-phrase library scored by token coverage + character-trigram cosine: lexical and deterministic. The approved plan and the v2 tag message call it "semantic"; we chose lexical because of the 512 MB / no-GPU limit and to keep a diffable YAML;
- clause-scoped history, denial and media suppression;
- Hinglish and typo normalisation;
- a structural symptom-report layer;
- a first-person acute-concern net that fails closed.

All of it runs pre-LLM on every question. **We chose it over** fine-tuning a small classifier or embedding model, **because** we have no labelled data for our patients, the free tier has 512 MB and no GPU, and a reviewer can read a YAML policy diff but cannot read a weight diff.

- **Alternatives rejected.**
  - *Fine-tuned classifier.* No training data, no GPU, and not auditable.
  - *Embedding classifier.* Model weights in a 512 MB container, plus nondeterminism across library versions.
  - *LLM as the pre-rail.* It needs a key, so it would not be keyless in CI, and it adds latency and cost before triage.
- **Constraint.** Data availability, the free-tier 512 MB memory limit, and auditability.
- **Evidence: the limit is real and we measured it.**
  - Blind battery 3 (50 emergencies + 50 benign near-misses, written by an evaluator that never read the rail code) against v6 and v7: **recall 44/50 (88%)**; against v8: **45/50 (90%)**; **false escalation 5/50 (10%)** and **0 emergencies answered** on the keyless path at all three.
  - Earlier batteries at the version each was blind to: battery 1 at v4 34/40 (85%); battery 2 at v5 42/50 (84%).
  - On batteries those versions never saw, recall moves a few points per release: battery 3 scores 39/50 at v4, 41/50 at v5, 44/50 at v6, 44/50 at v7, 45/50 at v8. v5 did not materially move fresh-wording recall over v4; a lexical rail gains slowly on wording nobody has seen.
- **Failure mode it creates.**
  1. About 1 in 10 freshly worded emergencies is missed by the rail. Battery-3 misses at v6 and v7: indirect self-harm (escalates at v8), Hinglish child poisoning, Hinglish GI bleed, Hinglish pre-eclampsia, cord prolapse, fever on chemotherapy.
  2. Over-escalation on hard benign near-misses: 10% on battery 3 (fiction, news and past-event framing), 26% on battery 2, against 7.3% on the committed set.
  3. On the LLM path a rail miss reaches the model. In a worst-case stand-in (always-confident reasoner, always-agreeing verifier; `score_blind --stand-in confident`) battery 3's rail misses end in ANSWER: 6/50 at v6 and v7, 5/50 at v8.
- **Mitigation.**
  - On the keyless path a rail miss has never become an ANSWER (0 of 140 blind emergencies). Every miss ended in a CLARIFY redirect ending with "If this is an emergency, call 112 (India) or your local emergency number now."
  - Redirects that name a danger concept or a present symptom are flagged `needs_review` and listed for the doctor (review queue, no page). That caught all 8 battery-2 misses at v5 but only 1 of battery 3's 6 at v6.
  - A final gate invariant means no question containing a danger concept, or (v6) a present body-state report, can end in ANSWER; the citation veto (v6) blocks answers citing anything outside the valid slice.
  - On the LLM path the Reasoner classifies `red_flag` itself, a second, independent chance to escalate. That is unmeasured: the live flow check sent four emergencies and the rails caught all four before the model, so none of the rail misses has reached the real model.
  - We disclose the limit instead of tuning the battery away.

## 4. Fail-closed ESCALATE over a best-effort answer

**We chose** to resolve any missing dependency, unavailable model, low confidence, high risk, superseded citation or ungroundable question to ESCALATE or CLARIFY, never a guess, **over** answering with a confidence disclaimer, **because** in this domain a wrong answer is a safety incident while an unnecessary escalation costs a doctor's minute.

- **Alternatives rejected.**
  - *Answer with "low confidence" wording.* Patients act on the answer, not on the hedge.
  - *Retry with a different prompt until confident.* That amounts to optimising for an answer, not for safety.
- **Constraint.** The safety rule.
- **Evidence.**
  - Gate at v8: 0 missed emergencies, 0 cross-patient leaks, 0 superseded leaks, 0 injection items answered, 0 ungrounded answers.
- **Cost of the decision (measured).** On the keyless spine, every medication-grounded in-scope answer escalates:
  - Medication facts carry risk weight 0.9 (`domain/scoring/risk.py`). A medication question scores risk **0.78**, above the **0.75** ceiling (`domain/thresholds.py`).
  - "What is the dose of my paracetamol?" returns `escalate` with "Risk too high (0.78)". We reproduced this with `POST /demo/ask`.
  - Together with the twins' inability to paraphrase, this is why keyless in-scope accuracy is **0.167**. It is deliberate, not a bug.
  - The LLM path uses the same blend (0.7 × fact-kind weight + 0.3 × the proposal's own risk), so a medication answer passes when the model reports risk ≤ 0.4. But the schema field defaults to **0.0** and the reasoner prompt never asks for it, so a model that omits it gets 0.63 and is answered. In both live runs both dosing questions grounded in a current fact were answered (2/2). On the LLM path the risk ceiling does little for medication answers; the verifier, citation veto and answer-text grounding check bound them.
- **Failure mode it creates.** Doctor-queue flooding trains clinicians to ignore escalations, which is itself a hazard.
- **Mitigation.** Over-escalation is a hard gate (≤ 15%; 7.3% at v8). The online monitor tracks a low-risk-escalation proxy, and small talk and non-clinical questions are redirected rather than escalated (commit `7c8acf3`).

## 5. Two LLM calls (Reasoner + independent Verifier) over one

**We chose** a Reasoner call that proposes an answer with cited fact ids, followed by a separate Verifier call that sees only the cited facts and can veto, **over** a single call that answers and self-checks, **because** a model grading its own output in the same context shares its own hallucination. An independent second pass with a narrower input is a cheap structural check on grounding, and grounding is the property that keeps a superseded or cross-patient fact out of an answer.

- **Alternatives rejected.**
  - *Single call with "cite your sources".* Citations get invented.
  - *Deterministic citation check only.* It catches fake ids but not an answer that misstates a real fact.
- **Constraint.** The safety rule. Cost pushed back the other way, and the price stayed acceptable.
- **Evidence.**
  - Pre-run estimate: Reasoner ≈ $0.000197 per call and Verifier ≈ $0.000101 per call on gpt-4o-mini, so an answered request costs ≈ $0.000298, against ≈ $0.000197 for one call.
  - Measured (two live flow checks): $0.00031 / $0.00029 per model-handled question including the sampled judge; model-handled questions took 1.3–5.3 s end to end with the two sequential calls.
  - The Verifier runs only when the Reasoner proposes an answer. Red-flag escalations make no LLM call at all ($0).
- **Failure mode it creates.**
  1. Roughly 1.5× tokens and a second network round trip on answered turns, so higher p99.
  2. If both calls hit the same provider outage, there is no answer at all.
- **Mitigation.** The second round trip only happens on the minority of turns that answer. On an outage both calls raise `ReasonerUnavailable` and the turn fails closed to ESCALATE, counted as `fail_closed_rate` on `/monitoring` with an alert above 5%. The cost is real: on the two live runs the end-to-end p95/max was 3.6 s and 5.3 s, which **misses** the 3 s p99 target (n = 18 each). Running the Verifier in parallel with a draft answer, skipping it for low-risk fact kinds, or streaming would be the next steps.

## 6. gpt-4o-mini over stronger models

**We chose** gpt-4o-mini as the default Reasoner, Verifier and judge **over** gpt-4o, gpt-4.1-mini or Claude Haiku 4.5, **because** the model never decides the verdict. Deterministic gates decide; the model proposes a grounded sentence. With the budget at $20, the cheapest competent model buys the most eval runs.

- **Alternatives rejected, using the same token estimate** (`python -m scripts.cost_report --model <m> --judge-model <m>`, answered request = Reasoner + Verifier, ESTIMATE, re-run 2026-10-08):

| Model | Est. $ / answered request | Requests per $20 (worst type, incl. 20% judge) |
|---|---|---|
| **gpt-4o-mini** | **$0.000298** | **≈ 62,500** |
| gpt-4.1-mini | $0.000794 | ≈ 23,463 |
| claude-haiku-4-5 | $0.002186 | ≈ 8,532 |
| gpt-4o | $0.004965 | ≈ 3,752 |

- **Constraint.** Cost: $20 in total, including dev iterations and the LLM eval slice.
- **Failure mode it creates.** A weaker model may produce more unsupported paraphrases or misclassify scope. The judge is the same model family as the Reasoner, which risks self-preference bias.
- **Mitigation.**
  - The Verifier and the deterministic gates bound the damage: an answer that cites nothing valid cannot pass.
  - The model is a config value (`CARELINE_LLM_MODEL`), and the cost table makes a swap a reviewed number.
  - Judge calibration against a human-labelled subset is planned and not yet done.
  - **Measured once, for gpt-4o-mini only:** cost and latency from the live flow check (n = 18). Not measured: accuracy on the eval set for any model, and the other models' live cost and latency.

## 7. Shared-case regression vs a committed baseline over a moving baseline (the v3 lesson)

**We chose** to compare every enforced metric against a committed baseline JSON (`evals/reports/after-policy-v8.json`). The comparison is **recomputed over the case ids present in both runs**, using the `per_case` map in each report. On top of that:

- a deleted baseline case fails the gate;
- per-split minimum case counts are enforced (emergency ≥ 60, in_scope ≥ 80, out_of_scope ≥ 40, cross_patient ≥ 20, injection ≥ 30, superseded ≥ 20);
- in-scope accuracy may not drop.

**We chose this over** comparing aggregate rates against "whatever the last accepted run was", **because** aggregate comparisons can be gamed two ways: by growing or shrinking the denominator, or by bumping the baseline in the same push as the change.

- **The lesson that forced it (v3).**
  - Policy v3 (tag `release/red-flags-v3`) raised over-escalation from 0.0707 to 0.09375 against the v2 baseline, a regression the gate should have blocked.
  - It passed only because the same review batch (`1f7aaf2`) changed CI's `--baseline` from `after-policy-v2.json` to `after-policy-v3.json`.
  - The trade-off itself was defensible: we paid about 2 points of over-escalation for recall. The *process* was not, because nobody had to approve the override.
- **Alternatives rejected.**
  - *Aggregate-only comparison.* It reads set growth as improvement.
  - *Absolute thresholds only.* A metric could slide from 7% to 14.9% unnoticed.
- **Constraint.** Data growth: the set grew 250 → 287 → 329 → 339 → 376 → 381 → 391 across v4 to v8. v8 passed against the v7 baseline on 381 shared cases with 0 verdict changes, while its 10 new cases were gated by the absolute thresholds. The original 250 ids are frozen in `evals/reports/baseline-v0.case_ids.txt` so historical numbers stay reproducible (`--case-ids`).
- **Failure mode that remains.** Someone can still edit the baseline JSON in a PR.
- **Mitigation.**
  - A baseline bump is a visible diff in `backend/evals/reports/`.
  - Our stated rule: a baseline change is its own commit, with the regression it accepts written in the message.
  - **Not enforced by tooling yet:** `main` has no branch protection and no CODEOWNERS, and no PR has run CI (the gate failing on the `demo/blocked-by-eval-gate` push is the CI evidence). Both are pending (see `docs/OPERATIONS.md`).

## 8. Blind batteries over self-written probes (the "42/42" lesson)

**We chose** to quote generalisation only from **blind batteries**. A blind battery is written by an evaluator that has never read the rail code, it is written before it is scored, and it is scored once against exactly one policy version. **We chose this over** probe sets written by the people who then fix the rail, **because** a probe set you tune against stops measuring generalisation the moment you tune.

- **The lesson that forced it.**
  - After v2 we wrote a 42-probe "novel" battery (`tests/brain/test_red_flag_novel.py`) and reported v3 at 42/42.
  - But the battery was written red *before* v3, v3 was built to pass it, and some v3 patterns map one-to-one to probe strings.
  - 42/42 was a development-set fit, not evidence. In the review's per-category re-run against the v2 snapshot, v2 caught 0/21 of its novel emergencies.
  - An ad-hoc fresh battery in that review escalated 14/32. That run is not committed, which is exactly why we moved to committed blind batteries.
- **Protocol now** (`backend/evals/blind/README.md`):
  - battery-1 was blind to v4 and scored 34/40; its misses drove v5, so it is **dev data** from v5 on;
  - battery-2 was blind to v5 and scored 42/50; its misses drove v6, so it is **dev data** from v6 on (50/50 at v6 is a fit);
  - battery-3 was blind to v6 and scored 44/50; nobody opened it while building v7 (44/50) or v8 (45/50);
  - the next policy release needs a fresh battery-4.
- **Alternatives rejected.**
  - *Keep growing the committed eval set and quote it.* It measures coverage of the wording families we wrote.
  - *A public benchmark.* None exists for post-consultation follow-up triage, and copying one is disallowed.
- **Constraint.** Data availability: there is no independent labelled source of real patient phrasings.
- **Failure mode it creates.** Small n. With 50 emergencies the Wilson 95% interval is wide: 45/50 gives 79–96%, 44/50 gives 76–94%, 42/50 gives 72–92%, 34/40 gives 71–93%. Each battery is single-use, and all three were written by LLM evaluator agents, so their wording may be correlated.
- **Mitigation.** We quote each battery number with its n and the version it was blind to. We do not read the similar numbers across batteries as robustness: they show a lexical rail that moves a few points per release on unseen wording.

## 9. Render free tier over Fly.io / Hugging Face Spaces

**We chose** a Render Docker web service defined as code in `render.yaml` (free plan, Singapore region, `/health` check, secrets as `sync: false` dashboard values, public-demo hardening on) **over** Fly.io or HF Spaces, **because** it costs $0, deploys the same Dockerfile we test locally, and keeps the deploy config reviewable in the repo.

- **Alternatives rejected.**
  - *Fly.io.* It needs a payment method on the org; we did not want a card attached to a public demo.
  - *HF Spaces.* It suits model demos, but our app is a FastAPI API plus a separate Next.js web app, and per-route secrets, CORS and rate-limit config are less natural there.
  - *Self-hosting a model (vLLM).* No GPU, and no accuracy gain at this scale.
- **Constraint.** Cost ($0 hosting), and the free tier's 512 MB RAM and single instance.
- **Failure mode it creates.**
  - **Cold start.** Render's free web services spin down when idle and the first request after that is slow. We have not measured it, because the service is not deployed yet.
  - Single instance, so no redundancy.
  - Ephemeral disk, so the usage JSONL and the in-process monitor reset on restart.
- **Mitigation.** We warm the URL before a demo (`curl /health`). The keyless local fallback gives identical behaviour (see `docs/DEMO-RUNBOOK.md`). The monitor and limiter state limits are documented in `docs/OPERATIONS.md`.
- **Status: not deployed.** The blueprint is ready, and no public URL exists today.

## 10. In-process monitor over Prometheus + Grafana

**We chose** an in-process online monitor (`careline/services/online_monitor.py`) fed one record per turn by `QuestionService`. It keeps bounded ring buffers (default 1,000 turns) and serves all five monitoring categories as JSON at doctor-authenticated `GET /monitoring`:

- operational latency, error and fail-closed rates;
- output verdict mix;
- quality from a sampled async LLM-as-judge;
- input drift (PSI/OOV against the eval reference);
- cost per request.

**We chose it over** a Prometheus exporter with a Grafana dashboard, **because** the deploy is one 512 MB container: a Prometheus server and Grafana are two more services we cannot host for free next to it, and every extra moving part is another way for a demo to fail.

- **Alternatives rejected.**
  - *Prometheus + Grafana Cloud free tier.* Feasible, but it needs a push gateway or agent in the container, and it does not give us the judge or drift logic anyway.
  - *Logs only.* There are no aggregates and no alerts.
- **Constraint.** Free-tier 512 MB, one container.
- **Evidence.** A local run after a 20 s load test returned all five sections. It also raised a real alert, `input drift: scope-mix PSI 0.64 > 0.2`, because the load generator's six-question rotation looks nothing like the eval mix. That alert is correct behaviour, not noise.
- **Failure mode it creates.** State is per process and lost on restart. There is no history, no paging, and alerts are only visible when someone polls.
- **Mitigation.** The monitor's interface is the seam. At scale the counters move to an external store or exporter (see `docs/OPERATIONS.md` §6). Langfuse traces hold per-turn history (live on Langfuse Cloud).

## 11. Langfuse over LangSmith

**We chose** Langfuse as the trace and cost store (`adapters/observability/langfuse_tracer.py`, `obs` extra, enabled by `CARELINE_LANGFUSE_PUBLIC_KEY` / `CARELINE_LANGFUSE_SECRET_KEY`) **over** LangSmith, which the agent's earlier tracing used, **because** Langfuse is open source and self-hostable. That matters for anything near PHI: a clinic could keep traces in its own region. It also has first-class generation cost fields, and its free cloud tier is enough for a demo.

- **Alternatives rejected.**
  - *LangSmith.* Hosted SaaS on the plan we had, with no self-host option for us.
  - *Arize Phoenix.* Good, but a second new tool to learn for no extra coverage.
- **Constraint.** PHI posture: the tracer sends a salted patient hash and never sends fact text. It does send the raw question text as trace input, which is acceptable only on fictional demo data; real patients would need it redacted or dropped.
- **Failure mode it creates.** An optional dependency fails silently.
  - In the first review the tracer was effectively dead code: the SDK was not installed and latency was hardcoded to 0.
  - That is fixed now: the `obs` extra exists, and the tracer has fake-client tests that assert the real model, latency and per-turn cost (`tests/llmops/test_usage_langfuse.py::test_v2_sends_real_model_latency_and_per_turn_cost`, `tests/llmops/test_wiring.py::test_langfuse_turn_gets_real_latency_usage_and_start`).
- **Status: done.** Traces export to Langfuse Cloud (SDK pinned to v3 via `langfuse>=2,<4`; v4 dropped `start_generation`). Live run 2 of the flow check exported 18 traces; canonical public trace: https://cloud.langfuse.com/project/cmuzr90o901dpad0htql4lfli/traces/112e8db367c1c28c0c4b86ffd1e444ec. `GET /monitoring` (the in-app Monitoring page) and the usage JSONL (`CARELINE_USAGE_LOG`) remain the aggregate evidence.

## 12. Per-doctor hashed credentials over SSO for the demo

**We chose** per-doctor password hashes in an environment variable: `CARELINE_DOCTOR_CREDENTIALS="dr-asha:pbkdf2_sha256$600000$..."`, generated with `python -m careline.adapters.auth.hash_password`. Around them sit:

- a 5-failures-per-account / 20-per-IP lockout for 15 minutes;
- a separate per-IP login rate window;
- role claims in every JWT.

Public-demo and production mode refuse the shared dev password and refuse dev-default secrets at startup. **We chose this over** SSO/OIDC, **because** a demo deployment has no identity provider, and SSO would be configuration nobody can exercise. The security problem we had to close was concrete: the first review found `POST /auth/token` minted a doctor JWT for any `doctor_id` with **no credential at all**.

- **Alternatives rejected.**
  - *One shared doctor password.* This was the intermediate fix. It opens every doctor id, so production refuses it now.
  - *SSO.* No IdP and no users.
- **Constraint.** Demo scope and time.
- **Failure mode it creates.**
  - No MFA, no password rotation UI.
  - The lockout is in memory and per process, so someone can deliberately lock a known patient out for 15 minutes. We accepted that as the safe failure.
- **Mitigation.** Covered by tests: `tests/api/test_doctor_credentials.py`, `test_login_lockout.py`, `test_public_demo_mode.py`. Production would move to an IdP; the `AuthService` port is the seam.

## 13. Shadow comparison + human merge over automatic promotion

**We chose** a side-by-side table that runs both policy versions over the eval set in one process (`scripts/shadow_compare.py`), with a human deciding the merge, **over** auto-promoting whichever version wins a metric, **because** a safety-policy release trades several metrics against each other. An auto-promoter optimising recall would accept a quiet over-escalation regression, which is exactly what v3 did (#7).

- **Constraint.** The safety rule: a release is multi-metric.
- **Failure mode it creates.** The comparison is only as exact as the arm reconstruction. A non-active arm with v3+ context layers is rebuilt **approximately**, because those layers are code, not data. The report says so.
- **Mitigation.** `--replay <tag>` (`baseline-v0`, `release/red-flags-v4`, `release/red-flags-v5`) runs that release's own gate on its own tree (byte-exact). The v1 arm reproduces baseline-v0's 58/60 misses exactly on the frozen ids.

## 14. Single-process budget guard over distributed rate limiting

**We chose** an in-memory guard in the ASGI middleware:

- a per-IP spend window (6/min on Render);
- a separate login window (10/min);
- a process-wide UTC daily cap (300 requests);
- a per-IP key taken from the rightmost trusted `X-Forwarded-For` hop, so a spoofed leftmost entry cannot rotate buckets.

**We chose it over** a Redis-backed distributed limiter, **because** the deploy is one container and Redis is another service and another outage mode.

- **Constraint.** The $20 budget is guaranteed by config (the daily cap), not by hope, and there is one free-tier container.
- **Failure mode it creates.** At more than one process each worker has its own counters, so the effective cap multiplies.
- **Mitigation.** It is documented as single-process, and the Dockerfile runs one uvicorn process. At scale the counters move to Redis, and the hard spend cap moves to the provider key's own limit (`docs/OPERATIONS.md` §6).

---

## Cut scope, and what replaced each

| Cut | Why | Replacement |
|---|---|---|
| Live auto-promote canary | Unmeasurable on demo traffic (#1) | Shadow comparison + gate (#13) |
| Vector-DB corpus RAG | Retrieval here is per-patient fact *validity*, not corpus search | Citation-groundedness + leak metrics, stated explicitly |
| Fine-tuned emergency model | No data, no GPU, not auditable | Versioned lexical + structural rail v1→v8 (#3) |
| vLLM self-hosting | No GPU; cost and ops with no accuracy gain at this scale | Hosted API (gpt-4o-mini) |
| Prometheus/Grafana | 512 MB, one container | In-process monitor at `GET /monitoring` (#10) |
| SSO | No IdP for a demo | Per-doctor PBKDF2 hashes + lockout (#12) |
| Cohen's κ second labeller | Not done yet | Safer-label tie-break rule; κ is listed as pending |
