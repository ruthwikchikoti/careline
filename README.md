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
two never disagree.

| # | Agent | Responsibility | Owner |
|---|---|---|---|
| 1 | Triage | pre-LLM red-flag rail | Vinay |
| 2 | Retrieval (RAG) | currently-valid slice of the record | Naga |
| 3 | Reasoner | propose a grounded candidate answer | Srujan |
| 4 | Verifier | independently veto unsupported answers | Srujan |
| 5 | Gatekeeper | confidence + risk gate → verdict | Vinay |
| 6 | Escalation | human handoff / live transfer | Vinay |
| 7 | Extraction (offline) | transcript → facts → doctor one-tap approval | Naresh |

## Run it
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q            # full suite, offline / keyless
```

The M0 brain runs **fully offline with no API key and no database**. Live LLM and
Mongo are added slice-by-slice (see the plan).

## Docs
Fuller product + planning documentation lives in [`../capstone/`](../capstone): PRD,
implementation plan, and each member's contribution notes. Working agreements for this
repo are in [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CLAUDE.md`](CLAUDE.md).

## Status
Current: **M0 scaffold + domain enums / Decision trace** (Ruthwik, RU-1/RU-2).
