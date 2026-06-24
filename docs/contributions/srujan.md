# Individual Contribution — Dharma Srujan Reddy

**Project:** CareLine — Post-consultation Multi-Agent AI Voice Agent (7-agent LangGraph system with a deterministic safety spine)
**Group:** HIve · **Repo:** https://github.com/ruthwikchikoti/careline
**Role:** Reasoning & Verification — the LLM agents (commit scope `llm` / `ui-answer`)
**Audit my slice:** `git log --author="srujan0404"` → 14 commits, 15–24 Jun, ~2,740 lines.

---

## 1. Summary

I own CareLine's **reasoning layer** — the boundary the LLM lives behind. I built the
**Reasoner** and **Verifier** agents as swappable adapters (offline heuristic twins,
Anthropic, and OpenAI), the structured-output handoffs every agent speaks in, and the
factory that selects a backend. Everything is **fail-closed** (uncertainty → escalate)
and **provider-agnostic** (swap Anthropic ↔ OpenAI with zero domain change). I later
added the **LLM Extraction agent**, the **grounded-answer UI panel**, and the **Patient
Record** screen + its backend endpoint.

---

## 2. What I built — the `llm` vertical (the core)

| # | Contribution | Files |
|---|---|---|
| SR-1 | **Reasoning ports** — `Reasoner`/`Verifier` ABCs + a single fail-closed signal `ReasonerUnavailable`; the structured handoffs `ClassifierProposal` / `VerificationResult` | `domain/ports/reasoning.py`, `domain/model/proposal.py` |
| SR-2 | **Keyless heuristic twins** — deterministic offline `HeuristicReasoner`/`HeuristicVerifier` so the entire suite runs with **no API key**; grounds strictly in the valid slice, declines/flags cross-condition, independent veto | `adapters/llm/heuristic.py`, `adapters/llm/_text.py` |
| SR-3 | **Structured-output DTOs** — `ProposalDTO`/`VerificationDTO` (`additionalProperties:false`, domain-enum vocab) + non-raising domain mappers | `adapters/llm/schemas.py` |
| SR-4 | **Guard-railed prompts** — frozen Reasoner/Verifier system prompts encoding the overriding rule; cache-friendly (facts-first, question-last) message builders | `adapters/llm/prompts.py` |
| SR-5 | **Anthropic adapter** — `AnthropicReasoner`/`AnthropicVerifier` via `messages.parse`; adaptive thinking + effort; **never** `temperature`/`top_p`/`budget_tokens`; lazy SDK import; any SDK error or `None`/refused parse → `ReasonerUnavailable` | `adapters/llm/anthropic_backend.py` |
| SR-6 | **Adapter factory** — selects reasoner/verifier backend from config/env; keyless heuristic default; **production guard** that refuses to ship the offline stub to real patients | `adapters/factory.py` |
| SR-7 | **OpenAI backend** — `OpenAIReasoner`/`OpenAIVerifier` (`gpt-5.5`, Responses API, `text_format`) + live smoke that skips without a key — proving the hexagonal boundary (a new provider = a new adapter, zero domain change) | `adapters/llm/openai_backend.py` |

Every task was **test-first**: the contract (structured mapping + fail-closed) is pinned
by a dedicated suite (`tests/llm/`) using injected fake clients, so it verifies **offline
and keyless**.

## 3. Additional contributions

- **LLM Extraction agent** (`#2`) — `OpenAIExtractor` (Responses API, `text_format`) that
  structures *any* transcript phrasing the regex twin misses ("continue paracetamol",
  "follow a soft diet"); the heuristic extractor stays the offline fallback, selected via
  `factory.build_extractor`. Same fail-closed contract.
  *(`adapters/llm/extraction_backend.py`)*
- **Grounded-answer panel** (`#6`) — the UI that makes an ANSWER trustworthy: the
  independent **verifier affirmation**, **confidence/risk** meters, the **cited valid
  facts** (resolved to their approved summaries), and reason/verify trace detail cards;
  wired into the Live Console. *(`components/answer/`, `components/citations/`,
  `app/console/_answer/`)*
- **Patient Record + history timeline** (`#4`) — built the backend
  `GET /patients/{id}/record` (valid slice + superseded history, tenant-scoped, no-leak)
  with `FactOut`/`PatientRecordOut`, **and** the Naga-side UI: record page, `FactCard`,
  `Timeline`, Patients landing, and a typed `getPatientRecord` client.
- **Build unblock** — fixed a duplicate-`headers` bug in the shared API client that was
  failing the whole web build.

## 4. Design decisions I made

- **Fail closed, always.** Any SDK error, missing dependency, or `None`/refused parse
  raises `ReasonerUnavailable` → the Brain escalates to the doctor. An LLM can never
  silently lower the safety bar.
- **Provider-agnostic via ports (hexagonal).** Adding OpenAI was a *new adapter with zero
  domain change* and the Anthropic adapter left intact — the boundary, proven.
- **Structured outputs only** — every inter-agent handoff is a validated Pydantic object,
  never free text; the model fills a strict schema and a mapper lifts it to the domain.
- **Never** `temperature`/`top_p`/`budget_tokens` — determinism and safety over knobs.
- **Keyless by default** — the heuristic twins make the whole system (and CI) run offline.

## 5. How my part connects to the whole

The Reasoner and Verifier are two nodes in Ruthwik's LangGraph; they consume Naga's
valid-slice facts, emit the structured proposal/verification that Vinay's gate chain
scores, and feed Naresh's pipeline. The grounded-answer panel renders that output for the
patient; the Extraction agent feeds the doctor's one-tap approval.

## 6. Evidence

- **Commits:** 14 under `srujan0404`, dated 15–24 Jun (`git log --author="srujan0404"`).
- **Scope:** `backend/careline/adapters/llm/*`, `adapters/factory.py`,
  `domain/ports/reasoning.py`, `domain/model/proposal.py`, `web/components/answer|citations`,
  `app/console/_answer`, plus the `#4` record endpoint and UI.
- **Tests:** the `tests/llm/` suite (ports, proposal, heuristic, schemas, prompts,
  anthropic, openai, factory) + the patient-record API tests — all green, offline/keyless
  (backend: 253 passed, 2 skipped).

## 7. Rubric criteria I primarily serve

Tool use & integrations · Structured outputs · Multi-agent architecture (the Reasoner +
Verifier agents) · provider-agnostic adapter design.

## 8. My demo segment (~90 s)

Show a structured proposal handoff, the Verifier vetoing an unsupported candidate, a live
**Anthropic ↔ OpenAI backend swap**, and the grounded-answer panel rendering an ANSWER
with its citations + verifier badge in the Live Console.
