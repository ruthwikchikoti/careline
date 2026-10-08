# Operations — deploy, rollout, rollback, monitoring, scale

How CareLine is served, how a prompt or safety-policy change reaches users, how we undo one, and what we watch. Every section is marked **in place** or **pending**. Nothing pending is implied to be done.

All commands run from `backend/` unless noted. "Keyless env" means:

```bash
export CARELINE_MONGO_URI= OPENAI_API_KEY= ANTHROPIC_API_KEY= LANGSMITH_API_KEY= LANGSMITH_TRACING=false
```

---

## 1. Deployment strategy

| Concern | Choice | Status |
|---|---|---|
| Packaging | One Docker image (`backend/Dockerfile`, Python 3.12-slim base, non-root uid 10001, no secrets baked in). Installs `.[api,llm]`. | In place |
| Runtime | One uvicorn process: `uvicorn careline.api.app:create_app --factory --port 8000 --no-proxy-headers`. One process keeps the in-memory rate-limit, daily-cap and lockout counters meaningful. | In place |
| Host | Render free web service from the `render.yaml` blueprint (Docker runtime, Singapore region, `healthCheckPath: /health`). | **Pending: blueprint ready, not deployed. No public URL.** |
| Model serving | **Hosted API, no self-hosted model.** The Reasoner, Verifier and judge call OpenAI gpt-4o-mini over HTTPS with structured output. The Anthropic backend is selectable via `CARELINE_LLM_BACKEND`. No GPU and no weights in the container (512 MB free tier). | Adapter in place; **never run live** (no valid key) |
| Default mode | **Keyless.** With no `OPENAI_API_KEY`, the deterministic spine runs: pre-LLM rails, retrieval, offline heuristic Reasoner/Verifier twins, gate chain. It is safe because it fails closed: medication questions escalate (risk 0.78 > ceiling 0.75). | In place |
| Secrets | `sync: false` dashboard values: `CARELINE_JWT_SECRET`, `CARELINE_INTERNAL_API_KEY`, `CARELINE_PIN_HMAC_SECRET`, `CARELINE_DOCTOR_CREDENTIALS`, optional `OPENAI_API_KEY`. `CARELINE_PUBLIC_DEMO=true` refuses to start with dev-default secrets or the shared dev doctor password. | In place (config) |
| Budget guards | 6 spend-bearing requests/min per IP, a separate login window of 10/min, a daily cap of 300 (UTC), and login lockout (5 per account / 20 per IP → 900 s). | In place (config) |
| Web | Next.js app (`web/`), talks to the API over HTTPS/JSON. `CARELINE_ALLOWED_ORIGINS` sets CORS. | Local only |

**Why keyless by default in a deployed demo.** The public URL must not spend money because a stranger clicked it, and the safety behaviour (rails, gates, fail-closed) is identical with or without a key. The key only buys better *answers*. The cost is that the keyless deploy answers few questions (in-scope answer accuracy 0.176 on the eval set). That is the honest trade-off, stated on the demo, not hidden.

## 2. What a release is (versioned artifacts)

A release is a commit that changes one or more versioned artifacts. All of them are recorded in `backend/prompts/manifest.yaml` with a version and a 12-character content hash:

| Artifact | File | Active stamp | How it takes effect |
|---|---|---|---|
| Reasoner prompt | `prompts/reasoner/v1.md` | `reasoner@v1+ba6c88c5ff53` | Loaded from the manifest at import (runtime pointer) |
| Verifier prompt | `prompts/verifier/v1.md` | `verifier@v1+e2a3ae5a5d38` | Runtime pointer |
| Extractor prompt | `prompts/extractor/v1.md` | `extractor@v1+aa7c75f999bd` | Runtime pointer |
| Judge prompt | `prompts/judge/v1.md` | `judge@v1+f7f494360114` | Runtime pointer |
| Red-flag policy | `policies/red-flags.v5.yaml` | `red_flags@v5+d7897fb5e5ab` | **Mirrors code.** The rail patterns live in `domain/rails/*.py` (the domain stays pure, with no file I/O). `tests/llm/test_prompt_registry.py` fails if YAML and code diverge. |

Fail-closed properties:

- A hash mismatch, a missing file or an unparseable manifest raises at import.
- Every eval report and trace carries the active stamps, so any number can be traced to exact artifact versions.

Policy history (each release a gated commit on `main`):

| Version | Commit | What changed | Evidence |
|---|---|---|---|
| v1 | (baseline-v0, `b8476c9` gate) | Literal regexes only | 58/60 emergencies missed: `evals/reports/baseline-v0-blocked.md` |
| v2 | `0de0f74` | Lexical danger-phrase library | `evals/reports/after-policy-v2.md` |
| v3 | `16c741a` | Context suppression + acute-concern net | `after-policy-v3.md` (passed via a baseline bump, see TRADEOFFS #7) |
| v4 | `c0275d3`, `149009e` | Adversarial review rounds 1 + 2: rails on every question, clause-scoped suppression, Hinglish/typo normalisation, 112 line on every redirect, structural symptom-report layer, danger-never-ANSWERs invariant | `after-policy-v4.md`; blind battery 1: 34/40 |
| v5 | `4ce353e` | Blind battery 1 as dev data: overdose/stockpiling, Indian-English and Hinglish deficits, media framing | `after-policy-v5.md`; blind battery 2: 42/50 |

## 3. Rollout

```
author change ─▶ PR ─▶ CI: Suite (keyless) + Eval gate (deterministic slice)
                         │      └─ shared-case regression vs after-policy-vN.json
                         ├─ shadow_compare --a vN --b vN+1 (table pasted in PR)
                         ├─ blind battery (fresh, once) for a policy release
                         └─ optional: LLM slice (push only, continue-on-error)
               ─▶ human merge ─▶ tag release/red-flags-vN+1 ─▶ deploy (Render)
```

| Step | Command / mechanism | Status |
|---|---|---|
| 1. Gate | `python -m careline.services.eval_gate --baseline evals/reports/after-policy-v5.json` (CI job "Eval gate (deterministic slice)"). Exit 1 on any absolute or regression trip. | In place. **No PR has run it yet; it has only run on pushes.** |
| 2. Shadow comparison (canary substitute) | `python -m scripts.shadow_compare --a v5 --b <candidate> --markdown evals/reports/shadow-v5-vs-<candidate>.md`. Promote only if every safety metric is B-or-equal and the gate passes on B. | In place |
| 3. Blind check (policy releases) | A fresh battery from an evaluator who has not read the rails, then `python -m scripts.score_blind evals/blind/battery-N.json`. Record the result against the one version it was blind to. | In place (battery-3 needed for the next release) |
| 4. New baseline | Commit `evals/reports/after-policy-vN+1.json` + `.md`, and point `ci.yml --baseline` at it **in a separate commit whose message names any regression accepted**. | Rule documented; not enforced by tooling |
| 5. Merge | Human merge after green checks. | **Pending: `main` has no branch protection.** Required checks to configure: "Suite (keyless)", "Eval gate (deterministic slice)". Add CODEOWNERS on `backend/evals/reports/`, `backend/policies/`, `backend/prompts/`, `.github/workflows/`. |
| 6. Tag | `git tag -a release/red-flags-v5 4ce353e -m "red_flags@v5+d7897fb5e5ab — gate PASS, blind-2 42/50"` then `git push origin --tags`. | **Pending: the repo has no tags yet.** Retro-tag the history: `release/baseline-v0` `b8476c9`, `release/red-flags-v2` `0de0f74`, `-v3` `16c741a`, `-v4` `149009e`, `-v5` `4ce353e`. |
| 7. Deploy | Render builds the Docker image from `main`. | **Pending.** `render.yaml` has `autoDeploy: true`, which deploys every push in parallel with CI. Change it to `autoDeployTrigger: checksPass` so a deploy waits for green checks. |

## 4. Rollback

Three levels, from fastest to most thorough. In a safety system a rollback is itself a release: it changes recall, so it goes through the gate too, against the *older* baseline.

### 4a. Immediate: Render previous deploy (seconds, no build)

Render dashboard → `careline-demo` → **Events / Deploys** → choose the last good deploy → **Rollback**. This restores the previous image without touching git. Then do 4b, so `main` matches what is serving; otherwise the next push re-deploys the bad version.

*Status: available once deployed. Never exercised (not deployed).*

### 4b. Code: revert the release commit(s)

Example: roll red-flags v5 back to v4. The v5 release is the test commit `9496c85` plus the release commit `4ce353e`.

```bash
git checkout -b rollback/red-flags-v4 main
git revert --no-edit 4ce353e 9496c85    # restores v4 rails, v4 YAML + manifest, v4 CI baseline, removes the 10 blind-1 eval items
cd backend
python -m pytest -q
python -m careline.services.eval_gate --baseline evals/reports/after-policy-v4.json
git push -u origin rollback/red-flags-v4  # open a PR; merge on green; Render redeploys
```

**Dry run (done in a throwaway worktree, 8 Oct 2026, keyless).**

- The revert applies cleanly.
- Active stamp becomes `red_flags@v4+cb141d91b062`.
- Gate vs `after-policy-v4.json`: **PASS** (0 missed, over-escalation 0.081).
- Suite: 1067 passed, 2 skipped, 1 env-only failure (`test_settings_mongo_uri_defaults_to_none`, which fails only because `CARELINE_MONGO_URI` is blanked).
- Gate runtime: ~1.1 s.

What it costs: rolling back reopens what v5 fixed. On the full 339-item set, `shadow_compare --a v4 --b v5` shows the v4 arm missing 24 emergencies. That arm is approximate: v4's context layer is code and is off in that arm. The commit message must say this.

### 4c. Prompt-only rollback: move the manifest pointer

Prompts are runtime-loaded from the manifest, so a prompt release (say `reasoner` v2 → v1) is rolled back by editing `prompts/manifest.yaml`:

```yaml
reasoner:
  version: v1
  file: prompts/reasoner/v1.md
  sha256_12: ba6c88c5ff53      # must match the file, or import fails closed
```

Then run the gate and redeploy.

This does **not** work for the red-flag policy. That YAML mirrors code, and moving its pointer alone fails `tests/llm/test_prompt_registry.py` (YAML/code sync) by design. Use 4b for policy releases.

**Time-to-rollback.** Not measured on a live deploy. Locally, the gate plus suite take about 30 s. Render's deploy time is unknown until we deploy.

## 5. Monitoring plan (all five categories)

Online monitoring lives in `careline/services/online_monitor.py`. `QuestionService` feeds it one record per turn, it keeps bounded ring buffers (`CARELINE_MONITOR_WINDOW`, default 1,000 turns), and it is served as JSON at **`GET /monitoring`** (doctor JWT required). It never stores question text or patient ids, only aggregate features. Every monitor entry point swallows its own errors, so monitoring cannot break a clinical call.

| Category | Metric | Computed in | Visible at | Threshold / alert | Action |
|---|---|---|---|---|---|
| **Operational** | Latency p50/p95/p99, throughput | `online_monitor.py` (operational) | `GET /monitoring`; load-test report; Langfuse trace latency once configured | Requirement N5: HTTP p99 < 250 ms at concurrency 10 (local: 159.8 ms). N4: LLM path p99 < 3 s (not measured) | Profile; scale out (see §6) |
| Operational | Error rate (pipeline raised) | `online_monitor.py` | `/monitoring` `alerts[]` | > 1% | Check logs; roll back (§4) if it started at a deploy |
| Operational | Fail-closed rate (Reasoner/Verifier unavailable → escalated) | `online_monitor.py` | `/monitoring` `alerts[]` | > 5% | Provider status; switch `CARELINE_LLM_BACKEND`; spine keeps patients safe meanwhile |
| **Input** | Scope-category mix, mean tokens, OOV-token rate | `online_monitor.py` (drift) | `/monitoring` `drift` | Feeds drift | — |
| **Output** | Verdict mix (answer / clarify / escalate), escalation rate, low-risk-escalation proxy | `online_monitor.py` (output) | `/monitoring` `output`; per-turn audit at `GET /audit` | Offline: over-escalation ≤ 15% (gate). Online: proxy watched, no alert yet | Sample escalations; add eval items; tune via a gated release |
| **Quality (online)** | Faithfulness of sampled ANSWER turns via async LLM-as-judge (`judge@v1`) against only the cited, valid facts | `online_monitor.py` + `adapters/llm/judge.py` | `/monitoring` `quality` | < 90% faithful once ≥ 10 judged | Pull judged turns; add failing ones to the eval set; block next release until fixed |
| **Quality (offline)** | 8 absolute gates + regression + split floors on 339 items; LLM slice gates in-scope accuracy ≥ 0.85 and judge faithfulness ≥ 0.90 | `services/eval_gate.py`, `services/llm_eval.py` | CI job summary + `eval-gate-report` artifact; `evals/reports/*.md` | Any trip → exit 1 | PR fails; fix or document the override |
| **Drift** | PSI over the scope mix vs the eval reference; OOV rate vs baseline; length shift | `online_monitor.py` (drift) | `/monitoring` `drift` + `alerts[]` | PSI > 0.2 (`CARELINE_DRIFT_PSI`), OOV > baseline + 0.15, length shift > 50%; needs ≥ 30 turns | Sample recent questions; write a new blind battery from the drifted wording; re-score |
| **Cost** | Tokens and $ per request (Reasoner + Verifier + sampled judge), mean and p95 | `adapters/llm/usage.py` turn scope → `online_monitor.py` | `/monitoring` `cost`; usage JSONL (`CARELINE_USAGE_LOG`); `python -m scripts.cost_report --usage-log <file>` | Daily cap 300 requests (hard, 429) | Investigate the spike; lower caps |

**Online judge sampling.** `CARELINE_JUDGE_SAMPLE_RATE` (default 0.2) of ANSWER turns are judged on a background thread. A full queue drops samples (counted) and never blocks. Without a key, the deterministic keyless judge twin runs, so the pipeline is exercised offline. **The live gpt-4o-mini judge has never run.**

**Observed locally** (8 Oct 2026, after a 20 s, 2,671-request load test against `127.0.0.1:8010`):

- operational p50 12.1 ms / p99 84.8 ms (server-side, window of 1,000);
- 0 errors, 0 fail-closed;
- verdicts: answer 0.166, clarify 0.5, escalate 0.334;
- keyless judge 93/93 faithful;
- **drift alert fired:** scope-mix PSI 0.64 > 0.2, a true positive, since the load generator's six-question rotation does not resemble the eval distribution;
- cost $0 (keyless).

**Traces.** Langfuse (`adapters/observability/langfuse_tracer.py`, `pip install -e ".[obs]"`, `CARELINE_LANGFUSE_PUBLIC_KEY` / `CARELINE_LANGFUSE_SECRET_KEY` / `CARELINE_LANGFUSE_HOST`) sends:

- model;
- latency;
- per-turn cost;
- active artifact stamps;
- a salted patient hash, never fact text.

**Pending:** no Langfuse project or keys exist, so there is no trace link. The Docker image does not install `obs` yet.

**What is missing.** No pager or push alerting (alerts are visible when polled), no history beyond the window, and no dashboard UI for `/monitoring` (it is JSON).

## 6. What breaks at 10× and how we would scale

"10×" means roughly ten times today's demo load, around 100 concurrent callers.

| Order it breaks | Component | Why | Metric that shows it | Fix |
|---|---|---|---|---|
| 1 | **LLM provider latency (p99)** | Two sequential provider calls per answered turn (Reasoner + Verifier). Provider p99 dominates; the spine's own work is milliseconds (gate per-case p99 6.8 ms). No explicit client timeout (SDK default). | `/monitoring` latency p99, fail-closed rate | Explicit client timeout of ~10 s with one retry; async OpenAI client; cache identical (prompt-version, policy, facts, question) turns; keep red-flag turns LLM-free |
| 2 | **Threadpool saturation** | Sync pipeline on Starlette's default threadpool (~40 workers). Each blocked LLM call holds a worker. | Latency p99 climbs while CPU is idle | Async LLM client, or bigger pool + more processes |
| 3 | **Per-process state** | Rate limiter, daily cap, login lockout, monitor ring buffers and audit memory are in-process. A second worker or instance splits counters (cap × N) and fragments monitoring. | Cap exceeded; inconsistent `/monitoring` between instances | Move counters to Redis; export monitor aggregates (Prometheus/OTel); provider-key spend limit as the hard cap |
| 4 | **Per-patient Mongo reads** | Every turn reads the patient's facts and validity windows and writes audit turns. Indexed on `(doctor_id, patient_id)`, but a free Atlas tier and one connection pool won't hold. | Mongo latency, connection errors → ESCALATE (fail closed) | Paid tier; short-TTL cache of the valid slice keyed by patient + max `effective_from`; batch audit writes |
| 5 | **Cost** | ≈ $0.000320 per answered + judged request (ESTIMATE). At 10× the demo's 300/day cap, 3,000/day ≈ $0.96/day. | `/monitoring` cost, usage JSONL | Lower judge sample at volume; per-tenant quotas; budget alerts on the key |
| 6 | **Eval cost per PR** | $0 keyless. The LLM slice is ≈ 339 × ~$0.0003 ≈ $0.10 per uncached run (ESTIMATE). | CI spend | Cache keyed on artifact hashes; run the LLM slice on release PRs only |

**Load-test numbers today** (`python -m scripts.load_test --url http://127.0.0.1:<port>`, keyless spine through HTTP `POST /demo/ask`, concurrency 10, ≥ 20 s, one local uvicorn process; generator and server on the same host, Intel i5-13500H, 16 logical CPUs, Python 3.13.3, 8 Oct 2026). Two independent runs:

| Run | Requests | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| README run (canonical) | 2,644 / 20.06 s | **131.8 req/s** | 70.0 ms | 131.4 ms | **159.8 ms** | 0 |
| Re-run for this doc | 2,671 / 20.03 s | 133.4 req/s | 68.8 ms | 130.8 ms | 155.4 ms | 0 |

The two agree within about 3%. These numbers include the threadpool offload added in `47c8c7b`. An earlier 300-request, 0.5 s run (571 req/s, p99 42.1 ms) predates that change and is superseded. LLM-path latency is **not measured** (no live run).
