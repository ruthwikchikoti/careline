# CLAUDE.md: CareLine Ops

Instructions for AI assistants working in this repo. Read this before editing.
Product and pipeline overview: [`README.md`](README.md). Data flow:
[`docs/architecture.md`](docs/architecture.md). Eval protocol:
[`backend/evals/RUBRIC.md`](backend/evals/RUBRIC.md).

## What this is
**CareLine Ops** is an eval-gated release pipeline (versioned prompts and safety
policies, a CI eval gate, monitoring, cost capture and shadow comparison) built
around **CareLine**, our team's clinical follow-up agent and the system under
test. CareLine answers a patient's post-consultation question **only** from that
one patient's doctor-approved, currently-valid facts. It **escalates to the
doctor** whenever a question is serious, out of scope, stale or low-confidence.

## The overriding rule (every change must serve this)
> **Uncertainty always resolves toward ESCALATE. Never answer from a superseded
> fact. One patient per call, with zero cross-patient reachability (a
> cross-patient leak is a sev-0).**

A change that could let the agent answer when it should escalate, or surface a
superseded fact or another patient's fact, is wrong however clean the code is.

## Architecture (short)
```
START → triage → retrieve → reason → verify → gate ─┬─ ANSWER   → answer   → END
          │ (red flag → escalate; small talk → clarify) ├─ CLARIFY  → clarify  → END
          │  reason/verify unavailable → escalate       └─ ESCALATE → escalate → END
```
- **5 agent nodes plus 3 terminal nodes** in a compiled LangGraph `StateGraph`.
  The escalation handoff (`QuestionService` → `TelephonyPort`) and extraction
  (`ExtractionService`) are services, not graph nodes.
- **The domain owns safety.** The Brain and the graph share `run_triage` and
  `run_gate_chain`. The graph re-implements no gate logic, and the parity tests
  (`tests/brain/test_parity*.py`) must stay green.
- **Layer 1** is MongoDB or in-memory. Each fact carries a half-open validity
  window and an approval stamp, and every read is scoped by
  `(doctor_id, patient_id)`. **Layer 2** (`MemoryProvider`) is indexed on
  approval but **not read on the decision path**. Do not describe it as
  retrieval unless you wire it in, with Layer-1 re-validation of each hit.
- **Every inter-agent handoff is a structured Pydantic object**, never free text.
- **The rails are code constants.** `policies/red-flags.vN.yaml` mirrors them
  (a test checks this) but is not loaded at runtime.

## Layout
```
backend/careline/
  domain/        pure business rules, no I/O: enums, model, brain (+ triage), rails, gates, scoring, ports
  adapters/      orchestration (LangGraph), llm (OpenAI/Anthropic/heuristic, judge, usage, registry),
                 mongo, memory, auth, telephony (stub), observability (Langfuse)
  services/      question_service, eval_gate, llm_eval, online_monitor, audit, auth, approval, dpdp, ...
  api/           FastAPI app + routers (auth, patients, consultations, brain, observability, monitoring, patient_portal)
backend/prompts/    versioned prompts + manifest.yaml (pins, sha256_12, changelog)
backend/policies/   red-flags.v1..v7.yaml
backend/evals/      cases/ (381 items), blind/ (blind batteries), reports/ (gate reports per release)
backend/scripts/    seed_demo, shadow_compare, score_blind, load_test, cost_report
backend/tests/      offline, keyless pytest suite
web/                Next.js doctor console + patient portal
```

## How to run
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                       # must be green before every commit; offline + keyless
python -m careline.services.eval_gate     # must PASS before any rail/gate/prompt/eval change lands
```
Everything runs with **no API key and no database**. The suite is hermetic:
`tests/conftest.py` blanks the provider / tracing keys, unsets
`CARELINE_MONGO_URI`, disables `load_dotenv` and stops `Settings()` reading
`backend/.env` before any test module is imported, so plain `python -m pytest -q`
is green with or without a developer `.env`.

## Working agreements (must follow)
- **Owned paths:** edit only the files your area owns (see
  [`CONTRIBUTING.md`](CONTRIBUTING.md)).
- **Commits:** conventional `type(scope): summary` with a short `Refs: <TASK-ID>`
  footer. Safety-critical work (rails, gates, brain) is **test-first**, as a
  separate commit.
- **Green suite and gate before every commit.**
- **Fail closed.** On any error, missing context or unavailable dependency, the
  safe default is ESCALATE, never a guess.
- **Releases** follow the rollout steps in the README: failing probes first, then
  the policy YAML, the manifest re-hash, the gate report, and the CI baseline
  bump, all in one release commit.
- **Never move a baseline or relabel an eval item silently.** State the before
  and after numbers in the commit message.
- **Honest numbers only.** Quote a number only from a committed report or a
  command you ran. An eval item or probe written after the fix is dev data and
  is never quoted as generalisation. Blind-battery numbers are quoted only for
  the version the battery was blind to.
- **Commits carry human authors only.** Coding agents never add themselves to a
  commit: no `Co-Authored-By` trailers, session links or "generated with" lines.

## Status (2026-10-08)
Active: `red_flags@v7+93b8295ea3c0` (on disk, not yet committed or tagged; v6
is also uncommitted), reasoner/verifier/extractor/judge v1. The 381-item gate
passes, and against the v6 baseline it passes with 0 verdict changes on the 376
shared cases. CI baseline: `after-policy-v7.json`. Keyless suite: 1512 passed,
2 skipped (with or without the env prefix). v7 adds the final red-team rail
families and a deterministic **answer-text grounding check** in the gate chain
(every dose / number / drug name in an ANSWER must appear in a cited current
fact). Blind battery 3 (blind to v6 and v7): 44/50 recall, 5/50 false
escalation, 0 emergencies answered on the keyless path at both versions.
Battery 2 is dev data from v6 on (its honest number stays 42/50 at v5). On a
worst-case LLM-path stand-in, battery 3's 6 rail misses still end in ANSWER at
v7; only 1 of the 6 lands in the doctor review queue (measured at v6).
LLM-path protection is measured only with stand-ins (no live key).

Pending:
- commit v6 and v7 + product fixes; tag `release/red-flags-v2`,
  `release/red-flags-v3`, `release/red-flags-v6`, `release/red-flags-v7`
- live LLM-slice run (needs a valid key; the local `.env` key is rejected)
- Langfuse project
- public deploy; `autoDeployTrigger: checksPass` in `render.yaml`
- branch protection and a blocked-PR screenshot
- Cohen's κ
- rotate the Atlas password (it was printed in local tooling output before the test_config fix)
- set `OpenAI(timeout=10, max_retries=1)`; refuse the default `CARELINE_TRACE_SALT` in public demo
- battery 4 before any v8 is quoted (battery 3 is blind to v7 too, and v7 did not move it)
