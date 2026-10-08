# CareLine — Failure Analysis

This document has two parts:

1. **LLMOps pipeline failure modes.** The release-pipeline work this project centres on: the eval gate, versioned policy releases, the online monitor, and the deploy and API hardening. These are failures we found (several by running adversarial reviews on ourselves) or designed against. Each has a detection mechanism, a mitigation, the test or command that pins it, and the risk that remains.
2. **Agent-level bugs.** Three bugs in the system under test (our team's clinical follow-up agent), found earlier while running it end to end. They are kept here because they explain why the gate measures over-escalation as well as recall.

All numbers can be reproduced keylessly from `backend/`. Commit hashes refer to `main`.

---

## Part 1 — LLMOps pipeline failure modes

### Summary

| # | Failure mode | Detection | Mitigation | Pinned by | Residual risk |
|---|---|---|---|---|---|
| 1 | Silent prompt/policy regression | Eval gate on every PR (`eval_gate`, exit 1) | 8 absolute gates + regression vs baseline | `--replay b8476c9` → 58/60 missed, exit 1 | Prompt text is not exercised by the keyless twins; `main` is unprotected |
| 2 | Baseline gaming (moving or diluted baseline) | Shared-case comparison over `per_case` ids | Intersection rescoring, split floors, deleted case fails | `tests/llmops/test_eval_gate_intersection.py`, `test_eval_gate_hardening.py` | The baseline JSON is editable in a PR (no CODEOWNERS) |
| 3 | Eval-set overfitting | Blind batteries scored once per version | Protocol in `evals/blind/README.md`; tuned batteries become dev data | `scripts/score_blind.py` | n = 50; ~84% ceiling; each battery is single-use |
| 4 | Gate failing open (vacuous splits) | Split-floor check | Per-split minimum counts | `test_split_below_floor_fails`, `test_split_missing_entirely_fails` | None known |
| 5 | LLM provider outage / timeout | `ReasonerUnavailable` → ESCALATE; `fail_closed_rate` on `/monitoring` | Fail closed; alert above 5% | `tests/brain/test_brain.py`, `test_graph.py`, `test_parity*.py` | No explicit client timeout (SDK default); a hung call holds a threadpool worker |
| 6 | Judge self-bias / small sample | Judge errors counted, never scored faithful; alert only at n_judged ≥ 10 | Judge sees only the cited, currently valid facts; versioned `judge@v1` prompt | `tests/llmops/test_judge.py`, `test_online_monitor.py` | Same model family as the Reasoner; not calibrated against humans; never run live |
| 7 | Cost runaway | Daily-cap 429s; cost section of `/monitoring`; usage JSONL | Per-IP spend window, daily cap, separate login window; red-flag turns make no LLM call; eval cache | `tests/api/test_rate_limit.py` | Counters are per process; no provider-side cap configured |
| 8 | Input drift | PSI / OOV / length vs the eval reference | Alert on `/monitoring` | `tests/llmops/test_online_monitor.py` | Poll-only, no paging; the reference is our own eval set |
| 9 | Cross-tenant leak (found in review) | Sev-0 repro test | Tenant-keyed audit queries + doctor-scoped portal login | `tests/api/test_patient_portal_isolation.py` | The Mongo sev-0 suite in `tests/data` skips without `mongomock_motor` |
| 10 | Passwordless doctor login (found in review) | Auth tests | Credential required → per-doctor PBKDF2 hashes | `tests/api/test_routers_auth.py`, `test_doctor_credentials.py` | No MFA; no rotation UI |
| 11 | PIN brute force | Lockout counters | 5 per account / 20 per IP → 15 min lockout; 6-digit PINs | `tests/api/test_login_lockout.py`, `test_seed_demo_pin.py` | A known account can be deliberately locked out |
| 12 | `X-Forwarded-For` spoofing of rate limits | Limiter tests | Key on the rightmost trusted hop; `--no-proxy-headers` | `tests/api/test_rate_limit.py`, `test_forwarded_proto.py` | A CDN in front would make clients share buckets (over-limits, never a bypass) |
| 13 | Event-loop blocking by the sync pipeline | Loop-probe tests | Question routes run on the threadpool | `tests/api/test_event_loop_offload.py` | Extraction route still calls a sync LLM inside `async` |
| 14 | Dead observability code (Langfuse) | Fake-client tests | `obs` extra; real model, latency and per-turn cost sent | `tests/llmops/test_usage_langfuse.py`, `test_wiring.py` | No Langfuse project or keys yet |
| 15 | Emergency rail misses fresh wording | Blind batteries | Danger-concept invariant; every redirect carries the 112 line | `tests/brain/test_review_round*.py`, `test_blind1_battery.py` | ~16% of blind emergencies are redirected, not escalated |
| 16 | Deploy not gated by CI | Review of `render.yaml` | Documented; fix specified | — | `render.yaml` still has `autoDeploy: true`; not deployed yet |
| 17 | Lost audit records under concurrency | Threaded tests | Re-entrant lock around every audit mutation | `tests/api/test_audit_concurrency.py` | A turn logged while the same history is cleared can reappear after restart |

### 1. Silent prompt or policy regression

- **What happened.** Baseline-v0 shipped a rail that caught 2 of 60 paraphrased emergencies. Nothing failed, because nothing measured it.
- **Detection.**
  - `python -m careline.services.eval_gate` runs all 339 items through the full Brain and exits 1 on any trip.
  - CI runs it on every PR touching `backend/**`, `.github/workflows/ci.yml` or `render.yaml`.
  - Replaying the original failure, `python -m scripts.shadow_compare --replay b8476c9`, gives `missed_emergencies: 58` and `replay_gate_exit_code: 1`.
- **Mitigation.**
  - Eight absolute gates (missed emergencies 0, cross-patient leaks 0, superseded leaks 0, injection answered 0, ungrounded answers 0, redirect accuracy ≥ 0.90, no-answer accuracy ≥ 0.95, over-escalation ≤ 0.15).
  - Regression against the committed baseline.
  - Artifacts are hash-stamped in `prompts/manifest.yaml`; a mismatch fails at import.
  - `tests/llm/test_prompt_registry.py` fails if the policy YAML and the domain constants diverge.
- **Residual risk.**
  - The keyless twins never execute prompt text, so a *prompt* edit passes the keyless slice. The LLM slice (`--mode llm`) would catch it, but **it has never run live**.
  - `main` has no branch protection, so the check fails but does not block a direct push. **No PR has ever run CI.**

### 2. Baseline gaming

- **What happened (the v3 lesson).** Policy v3 (`16c741a`) raised over-escalation from 0.0707 to 0.09375 against the v2 baseline. It passed only because the same review batch (`1f7aaf2`) changed CI's `--baseline` to `after-policy-v3.json`. The eval set has also grown 250 → 339, and an aggregate comparison would read growth as improvement or hide a regression in the new denominator.
- **Detection.** The gate recomputes every enforced metric over the case ids present in **both** runs (`per_case` in each report JSON).
- **Mitigation.**
  - Intersection rescoring.
  - A baseline case missing from the current run fails the gate.
  - Split floors (emergency ≥ 60, in_scope ≥ 80, out_of_scope ≥ 40, cross_patient ≥ 20, injection ≥ 30, superseded ≥ 20).
  - Keyless in-scope accuracy is regression-only: it may not drop.
  - The original 250 ids are frozen in `evals/reports/baseline-v0.case_ids.txt`.
- **Pinned by.** `tests/llmops/test_eval_gate_intersection.py::test_new_hard_cases_do_not_read_as_a_regression`, `tests/llmops/test_eval_gate_hardening.py::test_accuracy_drop_vs_baseline_fails`.
- **Residual risk.** A PR can still edit `evals/reports/after-policy-v5.json`. Our rule is that a baseline bump is its own commit and names the regression it accepts, but there is no CODEOWNERS file or required review to enforce that.

### 3. Eval-set overfitting

- **What happened.** We reported 42/42 on a "novel" probe battery after v3. The battery was written before v3 and v3 was built to pass it, so 42/42 was a fit.
- **Detection.** A blind battery, written by an evaluator that never read the rail code, scored once per policy version.
- **Mitigation and evidence.** `python -m scripts.score_blind evals/blind/battery-2.json` gives:

  | Battery | Blind to | Emergency recall | False escalation | Emergencies answered |
  |---|---|---|---|---|
  | battery-1 | v4 | 34/40 (85%) | 4/40 (10%) | 0 |
  | battery-2 | v5 | 42/50 (84%) | 13/50 (26%) | 0 |

  Battery-1 drove v5, so it is dev data now. Its v5 score is not quoted.
- **Residual risk.**
  - n is small: the Wilson 95% interval for 42/50 is 71–92%.
  - The committed benign split (7.9% over-escalation) is easier than real near-misses (26%).
  - The next release needs a fresh battery-3.

### 4. Gate failing open on vacuous splits

- **What happened.** In the first adversarial review (`1f7aaf2`) we found that a split with zero cases makes its gate vacuously true. Deleting the hard cases was the cheapest way to "pass".
- **Mitigation.** `SPLIT_MIN_CASES` in `eval_gate.py`. A split below its floor, or missing entirely, fails. Growth is allowed.
- **Pinned by.** `test_split_below_floor_fails`, `test_split_missing_entirely_fails`, `test_committed_eval_set_meets_floors`.

### 5. LLM provider outage

- **Detection.** The Reasoner and Verifier adapters raise `ReasonerUnavailable` on provider errors. The online monitor counts each such turn as `fail_closed`, and `GET /monitoring` alerts when `fail_closed_rate > 5%` or `error_rate > 1%`.
- **Mitigation.** Fail closed: the turn escalates to the doctor, never a guess. Red-flag escalations need no LLM, so emergencies are unaffected by an outage.
- **How it is measured.** As an error and fail-closed rate on `/monitoring`. We have **not** run an outage drill against the live provider (no live key).
- **Residual risk.** `OpenAI(api_key=...)` is built without an explicit `timeout`, so the SDK default applies. A hung provider call holds a threadpool worker for that long, and p99 would balloon before the error rate moved. **Fix:** set a timeout of about 10 s with one retry.

### 6. Judge self-bias and small samples

- **Design.**
  - The online judge samples `CARELINE_JUDGE_SAMPLE_RATE` (default 0.2) of ANSWER turns on a background thread.
  - It scores each answer only against the facts that turn cited, as valid at that turn.
  - Prompt `judge@v1` is hash-stamped. Judge errors count as errors, never as faithful. A full queue drops samples (counted) and never blocks a call.
  - The faithfulness alert (< 90%) fires only once at least 10 turns have been judged.
  - Offline, without a key, a deterministic keyless judge twin runs.
- **Residual risk.**
  - The judge is gpt-4o-mini, the same family as the Reasoner, so it may prefer its own phrasing.
  - It has not been calibrated against human labels.
  - 20% of a small demo's answers is a handful of turns.
  - **The live judge has never run.**
  - Plan: label a ~30-item subset by hand, measure judge–human agreement, and gate on judge faithfulness only once agreement is ≥ 0.8. Until then, gate on the deterministic metrics.

### 7. Cost runaway

- **Mitigation.**
  - The public demo (`render.yaml`) allows 6 spend-bearing requests per minute per IP and a process-wide daily cap of 300 (UTC reset). Logins have their own 10/min window and never count toward the cap.
  - Red-flag escalations make zero LLM calls.
  - The LLM eval slice caches every provider response on disk (`evals/.cache/`), so re-runs of unchanged prompt and policy versions are not billed.
  - Worst-case exposure at the cap: 300 × ≈ $0.000320 (answered + 20% judge, ESTIMATE) ≈ $0.10 per day.
- **Detection.** The cost section of `/monitoring` (mean and p95 $ per request), the usage JSONL (`CARELINE_USAGE_LOG`), and 429 counts.
- **Residual risk.** Counters are in memory and per process. No provider-side hard limit is configured on the key yet (it should be, as the real guarantee).

### 8. Input drift

- **Detection.** The monitor compares live turns with a keyless run of the eval set:
  - PSI over the scope-category mix (alert > 0.2, `CARELINE_DRIFT_PSI`);
  - OOV-token rate (alert at more than 0.15 above baseline);
  - mean length (alert at > 50% shift);
  - all evaluated once 30 or more turns are in the window.
- **Evidence.** After a 20 s local load test, `/monitoring` reported `input drift: scope-mix PSI 0.64 > 0.2`. The load generator's six-question rotation does not resemble the eval mix, so that is a true positive.
- **Residual risk.** Alerts are visible only on poll (no pager). The reference is our own eval distribution, which may not match real patients.

### 9. Cross-tenant leak (sev-0, found in our review)

- **What happened.** The same `patient_id` registered under two doctors could reach the other doctor's audit history through the patient portal. The portal login and audit queries were keyed by `patient_id` alone.
- **Mitigation.**
  - `237c3bd`: every patient-scoped audit query and the portal login are keyed by `(doctor_id, patient_id)`.
  - Patient login is now `{doctor_id, patient_id, pin}`.
  - Unique Mongo index on `patients(doctor_id, patient_id)`.
  - DPDP redaction is scoped to the requesting doctor.
- **Pinned by.** `tests/api/test_patient_portal_isolation.py` (written red first, `49ec139`).
- **Residual risk.** The Mongo-adapter sev-0 suite in `tests/data` is skipped in environments without `mongomock_motor`; it was one of the 2 skips in our run.

### 10. Passwordless doctor login (found in our review)

- **What happened.** `POST /auth/token` minted a doctor JWT for any `doctor_id` with no credential, which gave access to that doctor's patients, audit and erasure endpoints.
- **Mitigation.**
  - `8d24320` required a credential and added role claims (`doctor` / `patient`) to every token.
  - `3d97823` replaced the shared password with per-doctor PBKDF2-SHA256 hashes (600,000 iterations) in `CARELINE_DOCTOR_CREDENTIALS`.
  - Unknown ids do equal hashing work, so timing does not reveal which ids exist.
  - Production and `CARELINE_PUBLIC_DEMO=true` refuse to start with the shared dev password or dev-default secrets.
- **Pinned by.** `tests/api/test_doctor_credentials.py`, `test_routers_auth.py`, `test_public_demo_mode.py`.

### 11. PIN brute force

- **Mitigation.**
  - 5 failed logins per account (from any IP), or 20 per IP across accounts, lock out for 900 s. While locked, even a correct PIN gets 429.
  - Seeded demo PINs are 6 random digits.
- **Pinned by.** `tests/api/test_login_lockout.py`, `tests/api/test_seed_demo_pin.py`.
- **Residual risk.** Someone can lock a known patient out for 15 minutes. We accepted that as the safe failure. Registration still accepts 4–12 character PINs.

### 12. `X-Forwarded-For` spoofing

- **What happened.** uvicorn ran with `--proxy-headers --forwarded-allow-ips "*"`, which trusted the client-controlled leftmost XFF entry. Rotating a fake header bypassed every per-IP limit.
- **Mitigation.**
  - The server now runs with `--no-proxy-headers`.
  - Per-IP keys use the Nth-from-right hop (`CARELINE_TRUSTED_PROXY_HOPS=1` on Render).
  - `ForwardedProtoMiddleware` restores only the scheme (https) from the trusted hop.
- **Pinned by.** `tests/api/test_rate_limit.py`, `tests/api/test_forwarded_proto.py`.

### 13. Event-loop blocking

- **What happened.** Question routes were `async def` but called the synchronous pipeline, which includes blocking LLM HTTP calls. One slow provider call would have serialised every request, including `/health`.
- **Mitigation.** `47c8c7b` runs the question pipeline on the threadpool.
- **Pinned by.** `tests/api/test_event_loop_offload.py` (demo ask, internal run-question, patient ask).
- **Residual risk.** `ExtractionService.extract` (doctor-side consultation extraction) still calls the sync extractor inside an `async` method.

### 14. Dead observability code (Langfuse)

- **What happened.** The Langfuse tracer was effectively dead: the SDK was not a dependency, and the call site sent `latency_ms=0.0` and a hardcoded model name.
- **Mitigation.** An `obs` extra in `pyproject.toml`. The tracer now sends the resolved model, real latency and per-turn cost delta, and never raises into a clinical call.
- **Pinned by.** `tests/llmops/test_usage_langfuse.py::test_v2_sends_real_model_latency_and_per_turn_cost`, `test_tracer_never_raises_on_broken_client`, `tests/llmops/test_wiring.py::test_langfuse_turn_gets_real_latency_usage_and_start`.
- **Residual risk.** There is no Langfuse project or keys yet, so **there is no trace link to show**. The Docker image installs `.[api,llm]`, not `obs`.

### 15. Emergency rail misses fresh wording

- **Mitigation (v4 / v5).**
  - Emergency rails run on **every** question before the LLM.
  - History, denial and media suppression is clause-scoped and never suppresses a present-tense clause.
  - Hinglish and typo normalisation.
  - A structural symptom-report layer.
  - A final gate invariant: no question with a danger concept can end in ANSWER.
  - Every CLARIFY redirect ends with "If this is an emergency, call 112 (India) or your local emergency number now."
- **Residual risk.** About 16% of battery-2's emergencies were redirected rather than escalated. **0 were answered.** The misses are listed in `evals/blind/README.md`.

### 16. Deploy not gated by CI

- **Status.**
  - `render.yaml` still sets `autoDeploy: true`, so once the service is deployed, Render would deploy every push to `main` in parallel with CI, not after it.
  - **Fix specified:** `autoDeployTrigger: checksPass`, plus branch protection requiring "Suite (keyless)" and "Eval gate (deterministic slice)".
  - Both are pending; see `docs/OPERATIONS.md` §3.
  - The service is not deployed today, so no un-gated deploy has happened.

### 17. Lost audit records under concurrency

- **What happened.** Once the pipeline moved to the threadpool, concurrent `log_turn` calls could interleave. A test before the fix lost records (for example, 584 of 600 turns).
- **Mitigation.** `3d97823` added a re-entrant lock around every audit mutation. Durable writes happen outside the lock.
- **Pinned by.** `tests/api/test_audit_concurrency.py`.

---

## Part 2 — Agent-level bugs (system under test)

Three real bugs found while running the agent end to end, before the release pipeline existed. For each one: the trace evidence that exposed it, the exact fix, and the test that now locks it in. Commit references are real and present in this repo's history. Line numbers were correct at the time of each fix and may have moved since.

### How we debug

CareLine is built so that a failure is *legible*, not a black box. Two layers of
evidence drive every debugging session:

- **The always-on `ReasoningTrace`.** Every rail and gate appends a structured step to a
  per-turn trace — a step name, a `TraceStatus` (`PASS` for a step that let the turn
  proceed, `TERMINAL` for the step that decided the verdict, `SKIPPED` for a step
  short-circuited upstream; see `backend/careline/domain/enums.py:58`), a spec-section
  reference (e.g. `§5.1`), and a human-readable detail. This trace renders in the **Live
  Console** and the **Audit UI**, so when a turn does the wrong thing we can read exactly
  which gate fired and why, without a debugger.
- **LangSmith spans on the live path** (the agent's earlier tracer; the release pipeline's observability is `GET /monitoring` plus optional Langfuse, see Part 1 #8 and #14). The application service wraps the live LLM flow
  in nested spans — `question_service.run_question` → `reasoner.propose` →
  `verifier.verify` (`backend/careline/services/question_service.py:86,179,198`). When the
  ReasoningTrace says "low confidence → escalate", the spans tell us the actual reasoner
  and verifier confidences and citations that produced that number.

The three bugs below were each first *seen* in one of these surfaces (a flooded
escalation queue in the Audit UI; a confidence number in the spans below the floor) and
then traced to a specific line of domain logic.

---

### Bug 1 — Rich-record patients were escalated on every question

**Commit:** `3aae483` *fix(scoring): grounding measures citation validity, not coverage*

**Symptom.** Patients with a fuller approved record got *worse* answers, not better:
every question — even a clearly answerable one — fell below the 0.7 confidence floor and
was escalated to the doctor. A patient with two valid facts who asked about one of them
was punished for the fact they *didn't* ask about.

**Root cause.** The grounding component of the confidence score measured *coverage* of
the slice, not whether the citations were real. The original formula in
`backend/careline/domain/scoring/confidence.py` was:

```python
grounding = (
    min(1.0, len(proposal.citations) / valid_slice.count)
    if valid_slice.count > 0
    else 0.0
)
```

A legitimate one-fact answer against a four-fact record scored `1/4 = 0.25` grounding.
Fed into the weighted geometric mean (grounding weight `0.3`), that dragged the whole
score under the floor — so the more context a doctor approved, the more certain the
escalation.

**Fix.** Grounding now measures citation *validity*: of the facts the reasoner cited, how
many actually exist in the currently-valid slice (`confidence.py:111-117`):

```python
valid_ids = {fact.id for fact in valid_slice.facts}
citations = proposal.citations
grounding = (
    sum(1 for cid in citations if cid in valid_ids) / len(citations)
    if citations
    else 0.0
)
```

A correct one-fact answer now scores `1.0` grounding regardless of record size, while a
**fabricated or superseded** citation — the real hazard — still drags the score down, and
the verifier hard-zeros it independently. The safety property is preserved; only the
spurious penalty is gone.

**Test that locks it.** `tests/brain/test_bakeoff_safety.py::TestT5HappyPath`. Its
fixture `_seed_patient()` is a deliberately *rich* record — a valid medication (`med-1`),
a valid instruction (`instr-1`), plus two superseded facts. The test cites only `med-1`
and asserts `Verdict.ANSWER` (`test_bakeoff_safety.py:261-286`). Under the old formula
that single citation against a multi-fact slice would have scored `≈0.5` grounding and
escalated; the test now guarantees a rich record still answers.

---

### Bug 2 — Greetings and general questions flooded the doctor's queue

**Commit:** `7c8acf3` *fix(safety): stop escalating non-clinical input — greet small
talk, redirect out-of-scope*

**Symptom.** The escalation queue in the Audit UI filled with noise: a patient typing
"hey" or "good morning", or asking a general-knowledge question like "what is vitamin
C", produced a **doctor escalation**. Doctors were being paged for pleasantries, which
trains them to ignore the queue — itself a safety hazard.

**Root cause.** Two gaps. (1) There was no pre-LLM rail for small talk, so a greeting
reached the reasoner, was classified `out_of_scope` (no fact establishes "hey"), and hit
the scope gate. (2) The scope gate escalated *everything* out-of-scope:

```python
return Decision.escalate(
    "Question is outside the doctor's established scope for this patient.",
    scope=ScopeCategory.OUT_OF_SCOPE,
    risk=0.8,
    trace=ctx.trace,
)
```

**Fix.** A new narrow conversational rail catches *pure* small talk before the LLM and
returns a friendly CLARIFY instead of escalating (`backend/careline/domain/rails/
conversational.py`, wired into the Brain at `brain.py` right after the red-flag rail, so
"hey, I have chest pain" still escalates). `is_small_talk` only matches a message whose
every token is a greeting/filler word, so it can never swallow a real clinical question.
The scope gate's out-of-scope branch was changed from escalate to a CLARIFY redirect
(`backend/careline/domain/gates/chain.py:90`):

```python
return Decision.clarify(
    "I can only help with the care your doctor approved for you — your "
    "medicines, diet, and post-visit instructions. For anything else, please "
    "contact the clinic directly.",
    scope=ScopeCategory.OUT_OF_SCOPE,
    trace=ctx.trace,
)
```

Emergencies (red-flag) and cross-condition questions still escalate via their own rails
upstream — only genuinely non-clinical noise is redirected.

**Test that locks it.** `tests/brain/test_bakeoff_question_service.py::TestOutOfScope::
test_out_of_scope_redirects_without_escalating` — it runs an out-of-scope question with
the clarify budget already exhausted and asserts `Verdict.CLARIFY` with
`telephony.escalations == []` (previously this test, `TestClarifyBudget`, asserted
`ESCALATE`). The behavioural contract was inverted on purpose and is now pinned.

---

### Bug 3 — Over-correction: real clinical questions got cold-redirected

**Commit:** `aa64575` *fix(reason): route clinical-but-unanswerable to the doctor, not a
redirect*

**Symptom.** The Bug 2 fix over-shot. A genuine clinical question about *this patient's*
care that the approved facts couldn't fully answer (e.g. "is this new rash from my
medication?") was now classified `out_of_scope` and met with the polite "contact the
clinic" redirect — instead of reaching the doctor who should actually handle it.

**Root cause.** The reasoner's scope definitions conflated "we can't answer this" with
"this isn't about the patient's care." The prompt said simply:

```
- in_scope: fully answerable from the supplied facts.
- out_of_scope: the facts do not establish this.
```

So any unanswerable-but-clinical question fell into `out_of_scope` and got redirected.

**Fix.** Sharpened the scope split in `backend/careline/adapters/llm/prompts.py` so
*answerability* and *scope* are independent — in-scope means "about THIS patient's care,
even if unanswerable → a human doctor receives it"; out-of-scope means strictly
non-clinical general knowledge → redirect:

```
- in_scope: a clinical question about THIS patient's own medicines, diet,
  symptoms, or care instructions — EVEN IF the supplied facts don't fully answer it.
  ... still mark it in_scope and set candidate_answer to null, so a human doctor
  receives it.
- out_of_scope: NOT about this patient's clinical care — general medical/biology
  knowledge ("what is vitamin C"), other people, or unrelated/off-topic questions.
  These are redirected to the clinic, NOT sent to the doctor ...
```

And web turns now use a **zero clarify budget** so a clinical-but-unanswerable question
escalates straight to the doctor (who replies through the resolution loop) instead of
looping on "could you rephrase?" — `max_clarify_turns=0` in both
`backend/careline/api/routers/patient_portal.py:157` and
`backend/careline/combined.py` (`demo_ask`).

**Test that locks it.** The same `TestOutOfScope` guard from Bug 2 keeps *non-clinical*
input redirecting, while the clinical-unanswerable path is covered by the not-answerable
escalation tests — `tests/brain/test_brain.py::test_not_answerable_clarifies_then_
escalates_on_budget` and `test_empty_valid_slice_escalates` — which assert that an
in-scope question with no grounding reaches `ESCALATE` once the (now zero on web)
budget is spent.

---

### Meaningful improvements

Together these three fixes resolved a tension that made the early system unusable:

- **Rich-record patients now get answered**, not auto-escalated. Fixing the grounding
  metric (Bug 1) means the confidence floor reflects citation *validity*, so the more
  context a doctor approves, the better the agent answers — the intended behaviour.
- **The doctor's queue carries signal, not noise.** Greetings are greeted and
  general-knowledge questions are redirected (Bug 2), so escalations are now genuine
  clinical hand-offs a doctor will trust and act on.
- **Yet nothing clinical is dropped.** Bug 3 ensures that a real question about a
  patient's care that the facts can't answer still lands on the doctor, not a dead-end
  redirect.

Crucially, every fix kept the overriding rule intact: **uncertainty still resolves toward
ESCALATE.** Red-flag and cross-condition rails are untouched; the verifier veto and the
empty-slice / superseded-fact hard-zeros still force confidence to exactly `0.0`. We made
the system *usable* without trading away its fail-closed safety — and each change is
pinned by a test so the behaviour can't silently regress.
