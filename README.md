# CareLine

> A post-consultation AI **voice agent** that answers a patient's follow-up questions using **only the doctor's approved, currently-valid context** — and **escalates to the doctor** the instant anything is serious, out-of-scope, stale, or low-confidence. A **7-agent LangGraph system** with a deterministic safety spine, not a chatbot.

Capstone project · Course: **Multi-Agent Orchestration [AI/ML]**
**Team:** Ruthwik · Srujan · Naga · Naresh · Vinay

## The one rule
> **Uncertainty always resolves toward ESCALATE. Never answer from a superseded fact. One patient per call — zero cross-patient reachability.**

## Architecture at a glance
A compiled LangGraph `StateGraph` over a shared typed state:

```
START → triage → retrieve → reason → verify → gate ─┬─ ANSWER   → answer   → END
                                                     ├─ CLARIFY  → clarify  → END
                                                     └─ ESCALATE → escalate → END
```

The graph adds explicit agent nodes + observability but re-implements **no** safety
logic — it delegates to the headless `Brain`, and a **parity test** guarantees the
two never disagree. Of the seven roles, only the Reasoner and Verifier call an LLM
(and even those can run as deterministic offline twins); every routing decision is a
reviewable deterministic gate, never the model's.

| # | Agent | Responsibility | Owner |
|---|---|---|---|
| 1 | Triage | pre-LLM rails: red-flag, multi-condition, small-talk | Vinay |
| 2 | Retrieval | the currently-valid slice of the patient record | Naga |
| 3 | Reasoner | propose a candidate answer grounded in the valid slice | Srujan |
| 4 | Verifier | independently veto any unsupported answer | Srujan |
| 5 | Gatekeeper | scope + risk + confidence/staleness gates → verdict | Vinay |
| 6 | Escalation | human handoff / live transfer | Vinay |
| 7 | Extraction | transcript → facts → doctor one-tap approval (LLM or regex) | Naresh / Srujan |

## Repository layout
```
backend/                FastAPI + LangGraph safety spine (Python ≥ 3.11)
  careline/
    domain/             pure safety logic — brain, gates, rails, scoring, models, ports
    adapters/           I/O — langgraph orchestration, llm backends, mongo, memory, auth
    services/           use-cases — extraction, approval, question, audit, dpdp
    api/                FastAPI app + routers + DTOs
    demo_server.py      zero-setup, keyless demo console API (/demo/*)
  scripts/seed_demo.py  seed demo patients into Mongo
  tests/                offline / keyless pytest suite
web/                    Next.js doctor console + patient portal
docs/                   architecture, runbook, eval scenarios, contributions
```

## Prerequisites
- **Python ≥ 3.11**
- **Node ≥ 18** (for the web app)
- *Optional:* a MongoDB / Atlas URI for persistence, and an OpenAI **or** Anthropic API key for a live LLM. Neither is required — the system runs fully offline by default.

## Quickstart

### 1. Backend tests — offline, keyless, zero setup
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q            # full suite, offline / keyless
```
The M0 brain runs **with no API key and no database** — the heuristic twins stand in
for the LLM, and stores fall back to in-memory.

### 2. Run the API
Running a server needs the **`api`** extra (uvicorn + FastAPI), which `dev` does not include:
```bash
pip install -e ".[api]"        # add ,llm for a live LLM · ,data for Mongo · ,auth for JWT
```
Two entry points, both on http://localhost:8000:
```bash
# Full authenticated API — doctor + patient portals, Track A, and /demo/* (non-prod)
uvicorn careline.api.app:create_app --factory --reload

# Zero-setup demo console API — /demo/* only, always keyless
uvicorn careline.demo_server:app --reload
```

### 3. Web
```bash
cd web && npm install && npm run dev     # http://localhost:3000
```
The web app calls the API at `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`).

## Configuration
Copy `backend/.env.example` → `backend/.env` and fill in what you need. Everything is
optional — with an empty `.env` the system runs offline.

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | live OpenAI reasoner / verifier / extractor | — (offline twins) |
| `ANTHROPIC_API_KEY` | alternative live provider | — |
| `CARELINE_LLM_BACKEND` | force a backend: `openai` \| `anthropic` \| `heuristic` | auto-detect |
| `CARELINE_LLM_MODEL` | override the model id | `gpt-5.5` / `claude-opus-4-8` |
| `CARELINE_MONGO_URI` | Layer-1 persistence (MongoDB) | — (in-memory) |
| `LANGSMITH_API_KEY` | LangSmith tracing | — (no-op) |
| `CARELINE_JWT_SECRET` / `CARELINE_INTERNAL_API_KEY` / `CARELINE_PIN_HMAC_SECRET` | auth secrets (**must** be set in production) | dev defaults |

**Backend selection order:** explicit `CARELINE_LLM_BACKEND` → else `OPENAI_API_KEY` →
else `ANTHROPIC_API_KEY` → else the keyless heuristic twins. A production guard refuses
to start the offline stub when the environment is production.

> **Loading `.env`:** the demo server auto-loads `backend/.env`. The authenticated API
> reads `CARELINE_*` settings from `.env`, but non-prefixed keys (`OPENAI_API_KEY`,
> `LANGSMITH_*`) must be in the process environment — launch it with:
> ```bash
> set -a && source .env && set +a && uvicorn careline.api.app:create_app --factory --reload
> ```

## Seed demo data
With `CARELINE_MONGO_URI` set, populate the database the web UI reads:
```bash
cd backend && python -m scripts.seed_demo
```
Seeds 5 patients under doctor **`dr-asha`**, each a coherent post-consultation record
with current **and** superseded facts (so the history timeline and staleness safety have
something to show). Demo logins:
- **Doctor:** `dr-asha` (web doctor sign-in, or `POST /auth/token`)
- **Patient portal:** patient id `ravi-kumar`, PIN `1234`

## Web screens
- `/console` — **Live Agent Console**: ask a question, watch the trace stepper + verdict
- `/dashboard`, `/patients`, `/patients/[id]` — doctor roster, record, and history timeline
- `/consultations` — **Track A**: create → consent → extract → one-tap approve
- `/audit`, `/escalations` — compliance trail and closing the escalation loop
- `/eval` — live **T1–T8** safety-regression dashboard
- `/patient/login`, `/patient` — patient self-service portal

## Testing
```bash
cd backend && python -m pytest -q      # offline, keyless (~256 tests)
```
Key guarantees pinned by the suite:
- **Parity** — the graph and the headless Brain return identical verdicts (`tests/brain/test_parity.py`)
- **T1–T8 bakeoff** — the safety scenarios, also exposed live at `/eval` (`tests/brain/test_bakeoff_safety.py`)
- **Sev-0 isolation** — cross-patient reachability is blocked (`tests/data/test_isolation_sev0.py`)

> The suite is offline/keyless. If `backend/.env` sets `CARELINE_MONGO_URI`, some API
> tests will try to reach Mongo — run with `.env` absent (or the var unset) for a clean
> offline run.

## Status
**Backend:** full safety spine (Brain + LangGraph parity), Track A HITL pipeline
(LLM-or-heuristic extraction → doctor approval), REST API with JWT / internal-key auth,
patient portal, DPDP erasure, optional Mongo via `CARELINE_MONGO_URI`, and LangSmith tracing.
**Web:** doctor console + dashboard, patient record + timeline, consultation approval,
audit + escalations, live eval dashboard, and the patient portal.
