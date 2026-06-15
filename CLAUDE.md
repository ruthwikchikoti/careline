# CLAUDE.md — CareLine

Project-level instructions for AI assistants working in this repo. Read this before
editing. The full product/capstone docs are in [`../capstone/`](../capstone) (reference).

## What this is
**CareLine** — a post-consultation AI voice agent. After a consultation, patients call
with follow-up questions. CareLine answers **only** from the doctor's approved,
currently-valid context for that one patient, and **escalates to the doctor** the
instant anything is serious, out-of-scope, stale, or low-confidence. It is a **7-agent
LangGraph system** with a deterministic safety spine — explicitly **not** a chatbot.

## The overriding rule (every change must serve this)
> **Uncertainty always resolves toward ESCALATE. Never answer from a superseded fact.
> One patient per call — zero cross-patient reachability (a cross-patient leak is sev-0).**

If a change could let the agent answer when it should escalate, or surface a superseded
/ another patient's fact, it is wrong regardless of how clean the code is.

## Architecture
A compiled LangGraph `StateGraph` over one shared typed state:

```
START → triage → retrieve → reason → verify → gate ─┬─ ANSWER   → answer   → END
                                                     ├─ CLARIFY  → clarify  → END
                                                     └─ ESCALATE → escalate → END
```

- **Two-layer data.** Layer 1 = MongoDB source-of-truth, every fact carries half-open
  temporal validity (`effective_from <= now < superseded_at`) + a doctor-approval
  stamp. Layer 2 = a pluggable memory/RAG provider. **Memory proposes, source-of-truth
  disposes:** a retrieved fact only contributes to a confident answer if Layer 1
  confirms it is valid *now*.
- **Brain is the single safety authority; the graph delegates.** The LangGraph nodes
  add observability and explicit agents but re-implement no gate logic. A **parity
  test** asserts the graph and the headless `Brain` return identical verdicts.
- **Every inter-agent handoff is a structured Pydantic object** — never free text.

## Layout
```
backend/careline/
  domain/        pure business rules, no I/O (the safety authority)
    enums.py        Verdict, ScopeCategory, FactKind, TraceStatus   [Ruthwik]
    model/          Decision, ReasoningTrace, proposals, facts, ...
    brain/          Brain orchestrator pipeline                     [Ruthwik]
    rails/ gates/ scoring/   pre-LLM rails + gate chain + scoring   [Vinay]
    ports/          abstract ports (reasoning, memory, repos)
  adapters/      I/O + frameworks
    orchestration/  LangGraph graph                                 [Ruthwik]
    llm/            reasoner / verifier adapters                    [Srujan]
    memory/ mongo/  Layer-2 RAG + Layer-1 source-of-truth           [Naga]
  services/      application use-cases (extraction, approval, ...)  [Naresh/Vinay]
  api/           FastAPI app + routers                              [Naresh]
tests/           pytest suite (offline / keyless)
```

## How to run
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q        # must be green before every commit; offline + keyless
```
The M0 brain runs with **no API key and no database**. Live LLM / Mongo extras are
added slice-by-slice.

## Working agreements (must follow)
- **Owned paths:** edit only files your member owns (table in [`CONTRIBUTING.md`](CONTRIBUTING.md)).
- **Commits:** conventional `feat(scope): summary` + a short `Refs: <TASK-ID>` footer.
  Safety-critical work (rails / gates / brain) is **test-first as a separate commit**.
- **One member, one identity.** Each member commits under their own git name/email so
  it's clear who owns each vertical.
- **Green suite before every commit.**
- **Fail closed.** On any error, missing context, or unavailable dependency, the safe
  default is ESCALATE — never a guess.

## Status
Built incrementally against the team plan. Done: RU-1 scaffold, RU-2 enums +
Decision/trace. Next on the spine: RU-3 Brain pipeline → RU-4 LangGraph → RU-5
parity test.
