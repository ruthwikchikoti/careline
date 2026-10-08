# Interface contracts

These are the shapes other parts of the system depend on. Before you change a
field listed here, tell the owner of that interface. Tests pin some of these
shapes, but not all:

- `tests/brain/test_dpdp_service.py::TestInterfaceDriftGuard` checks `QuestionIn`,
  `AnswerOut` and `ErasureOut`.
- The router tests under `tests/api/` check the HTTP bodies and status codes.
- Nothing pins the other domain shapes (`Decision`, `MemoryHit`,
  `EscalationPayload`) except their own unit tests.

Every contract serves one rule: uncertainty always resolves toward **ESCALATE**.
Never answer from a superseded fact. One patient per call, with zero
cross-patient reachability.

---

## 1. Layer-1 source of truth

### `ValidSlice` (`domain/model/patient.py`)

| Field | Type | Notes |
|---|---|---|
| `as_of` | `datetime` | the instant the slice was computed |
| `facts` | `tuple[Fact, ...]` | approved facts that are valid now (`effective_from <= as_of < superseded_at`) |

### `PatientRepository` (`domain/ports/repositories.py`)

Every method is keyword-only and tenant-scoped by `doctor_id`. A wrong-tenant
read returns `None` or empty; it never raises a different error that would leak
whether the record exists.

| Method | Returns | Purpose |
|---|---|---|
| `get(doctor_id, patient_id)` | `Patient \| None` | the full aggregate |
| `exists(doctor_id, patient_id)` | `bool` | tenant-scoped existence check |
| `valid_slice(doctor_id, patient_id, now)` | `ValidSlice` | grounding context for reasoning |
| `history(doctor_id, patient_id, now)` | `tuple[Fact, ...]` | facts retired as of `now`, for audit and the record view |
| `add_facts` | `None` | append without supersession |
| `apply_facts` | `tuple[Fact, ...]` | the supersession write path (approval) |
| `soft_delete(doctor_id, patient_id)` | `int` | DPDP erasure: null the clinical text, keep the skeleton |
| `list_for_doctor(doctor_id)` | `list[(patient_id, approved_fact_count)]` | doctor console patient list |
| `find_identity(doctor_id, patient_id)` | `PatientIdentity \| None` | portal login lookup. **Scoped by doctor**: `patient_id` is unique only within a doctor (MongoDB has a unique index on `(doctor_id, patient_id)`) |
| `find_by_caller(...)` | `PatientIdentity \| None` | caller-id lookup for the voice path (doctor-scoped) |
| `upsert_identity(identity)` | `None` | register a caller id and `pin_hmac` |

The unscoped `find_by_patient_id` was removed. It let two doctors' patients with
the same id reach each other through the portal.

---

## 2. Layer-2 memory seam

### `MemoryProvider` (`domain/ports/memory.py`)

The namespace is always `(doctor_id, patient_id)`. There is no cross-patient
retrieval path.

| Method | Purpose | Called from |
|---|---|---|
| `index` | rebuild the namespace from the approved valid slice | `ApprovalService` |
| `retrieve` | up to `k` relevance hits for one patient | **not called on the decision path today.** The `retrieve` node ranks the Layer-1 valid slice directly (see `docs/architecture.md` §4) |
| `forget` | drop the namespace (DPDP erasure) | `DpdpService` |

`MemoryHit` has these fields: `fact_id: str`, `text: str`, `score: float`,
`kind: FactKind | None`.

---

## 3. Reasoning ports

### `Reasoner` / `Verifier` (`domain/ports/reasoning.py`)

| Port | Method | Input | Output |
|---|---|---|---|
| `Reasoner` | `propose` | `question`, `context: ValidSlice` (the ranked grounding subset) | `ClassifierProposal` |
| `Verifier` | `verify` | `question`, `proposal`, `context: ValidSlice` (the **full** valid slice) | `VerificationResult` |

**Fail closed.** An implementation must raise `ReasonerUnavailable` rather than
return a guess, and the graph turns that into ESCALATE. Live adapters use the
OpenAI Responses API (`responses.parse` with a strict Pydantic `text_format`) or
Anthropic `messages.parse`. A refused or unparseable response raises
`ReasonerUnavailable`.

---

## 4. Decision handoff

### `Decision` (`domain/model/decision.py`)

| Field | Type | Notes |
|---|---|---|
| `verdict` | `Verdict` | `answer` / `clarify` / `escalate` |
| `answer_text` | `str \| None` | the answer, or the clarify/redirect prompt. Every redirect and clarify ends with the emergency line |
| `escalation_reason` | `str \| None` | required on ESCALATE |
| `scope` | `ScopeCategory \| None` | |
| `confidence` | `float` | `[0, 1]` |
| `risk` | `float` | `[0, 1]` |
| `citations` | `list[str]` | fact ids supporting the answer |
| `trace` | `ReasoningTrace` | ordered `TraceStep(name, status, spec_section, detail)` |

Construct a `Decision` only through `Decision.answer()`, `.clarify()` or
`.escalate()`.

---

## 5. HTTP API

All bodies are JSON. Request bodies reject unknown fields
(`extra="forbid"`). Every JWT carries a `role` claim. A doctor route rejects any
token whose role is not `doctor`, and the patient portal rejects doctor tokens.

### Auth

| Route | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /auth/token` | none | `{doctor_id, password}` | `{access_token, token_type: "bearer"}` | `422` missing field. `401 "invalid doctor id or password"` is returned identically for a wrong password, an unknown id, the reserved demo id or an id outside the allowlist. `429` with `Retry-After` while locked out |
| `POST /patient/login` | none | `{doctor_id, patient_id, pin}` | `{access_token, token_type, patient_id, doctor_id}` | `422`. `401` is returned identically for an unknown doctor or patient, a reserved id or a wrong PIN. `429` with `Retry-After` while locked out (even with the correct PIN) |

Passwords are checked against per-doctor pbkdf2 hashes in
`CARELINE_DOCTOR_CREDENTIALS`. The shared `CARELINE_DOCTOR_PASSWORD` is a
development fallback that production and public-demo mode refuse.

Lockout triggers after 5 failures per account (from any IP) or 20 per client IP
(across all accounts), and lasts 900 s. Login routes also have their own per-IP
minute window, separate from the spend window.

### Patient portal (`Bearer` patient JWT; every route is scoped by the token's `(doctor_id, patient_id)`)

| Route | Request | Response |
|---|---|---|
| `GET /patient/me` | none | `CarePlanOut {patient_id, as_of, facts: [FactOut]}` |
| `POST /patient/ask` | `{question}` (1–2000 chars) | `PatientAnswerOut {turn_id, verdict, answer_text, escalation_reason, citations, patient_message, emergency}`. `escalation_reason` is always `null` here (the internal gate reason stays in the audit and the doctor's view). `patient_message` comes from `api/patient_text.py`: on a `red_flag` ESCALATE "This may be an emergency. …call 112… Your message has also been sent to your doctor."; on any other ESCALATE "Your question has been sent to your doctor, who will reply here." plus the 112 line; otherwise the answer or redirect text, unless it contains a decimal score or a gate word (risk, confidence, threshold, gate, verifier, reasoner, trace), in which case the escalation message (fail closed); `emergency` is true when the decision's scope is `red_flag` (the portal then shows the 112 banner). Runs on the threadpool with a clarify budget of 0. Counted as a spend route by the budget guard |
| `POST /patient/feedback` | `{turn_id, helpful: bool, comment?: str (≤ 500)}` | `200 {turn_id, helpful, comment, rated_at}`. Human online feedback on the patient's **own** ANSWER/CLARIFY turn; re-rating overwrites. Another patient's or tenant's turn → `404`; an ESCALATE turn → `409`. Feeds `/monitoring` `human_feedback` (no text) |
| `GET /patient/questions` | none | `[PatientQuestionOut {turn_id, asked_at, question, verdict, answer_text, escalated, doctor_reply, replied_at, patient_message, emergency}]` |
| `DELETE /patient/history` | none | `204` |

### Doctor console (`Bearer` doctor JWT; `doctor_id` always comes from the token)

| Route | Purpose |
|---|---|
| `POST /patients` | Register `{patient_id, caller_id, pin}`. The PIN must match `^[0-9]{6}$` (exactly 6 digits), else `422`. The seed prints one distinct 6-digit PIN per patient. Registering `demo-patient` returns `400` (reserved) |
| `GET /patients`, `GET /patients/{id}`, `GET /patients/{id}/record` | Patient list, summary, and record (`PatientRecordOut {current, history}`) |
| `DELETE /patients/{id}/data` | DPDP erasure → `ErasureOut {patient_id, layer1_nulled, layer2_dropped, audit_redacted}`. Audit redaction is scoped to the requesting doctor |
| `POST /consultations`, `/{id}/consent`, `/{id}/extract`, `/{id}/approve`; `GET /consultations[/{id}]` | Extraction and approval workflow (HITL) |
| `GET /audit`, `GET /audit/events` | Audit. Each turn carries `scope`, `needs_review` and `review_reason` (defaults keep older Mongo rows loadable) |
| `GET /escalations` | `EscalationsOut {…, waiting, patients_waiting, review_waiting, review: [AuditTurnOut]}`. `review` is this doctor's redirected (CLARIFY) turns flagged `needs_review`, newest first, tenant-scoped |
| `POST /escalations/{turn_id}/resolve` | Reply to and close one of this doctor's escalated **or review-flagged** turns. Any other turn id (another tenant's, or an unflagged redirect) is `404` |
| `POST /audit/turns/{turn_id}/review` | Doctor review (expert human eval): `{correct: bool, note?: str (≤ 1000)}` → `200 {turn_id, patient_id, verdict, correct, note, reviewed_at}`; re-review overwrites; another doctor's turn → `404`. `GET /audit` rows carry `reviewed, review_correct, review_note, reviewed_at` |
| `GET /eval` | Live re-run of the eight T1–T8 gate-chain scenarios at production thresholds (not the 391-item gate) |
| `GET /monitoring` | Online monitor snapshot: `{scope: "process-wide, aggregate, no PHI", generated_at, operational, output, quality, drift, cost, human_feedback, alerts}`. Aggregate only, with no question text and no patient ids. Process-wide: every doctor sees the deployment's aggregates, not a per-tenant slice |

### Internal and demo

| Route | Auth | Request | Response |
|---|---|---|---|
| `POST /internal/run-question` | `X-Internal-Key` (service principal) | `QuestionIn {doctor_id, patient_id, call_id, question}` | `AnswerOut {verdict, answer_text, escalation_reason, confidence, risk, citations, trace: [TraceStepOut]}` |
| `POST /demo/ask` | none (an optional doctor JWT lets it use a real `patient_id`) | `{question, patient_id?}` | `{verdict, answer_text, escalation_reason, patient_message, confidence, risk, citations, trace}`. The console shows `patient_message` as what the patient sees and labels `escalation_reason` as the doctor view |
| `GET /demo/patient` | none | — | the bundled fictional demo patient |
| `GET /health`, `GET /api/meta` | none | — | liveness, and the fictional-data banner |

`/demo/*` is mounted only when `CARELINE_ENVIRONMENT` is not `production`
(public-demo mode keeps it).

`/internal/run-question` is the one route that takes `doctor_id` from the
body. Its caller is a trusted service holding the internal key (the voice
path), not an end user.

---

## 6. Telephony escalation sink

### `EscalationPayload` (`adapters/telephony/stub.py`)

| Field | Type |
|---|---|
| `call_id` | `str` |
| `patient_id` | `str` |
| `doctor_id` | `str` |
| `reason` | `str` |
| `escalated_at` | `datetime` |
| `terminal_gate` | `str \| None` |

`TelephonyPort.escalate(payload)` starts the transfer to the doctor. It is
called by `QuestionService` on every ESCALATE. The implementation is an
in-memory stub; voice is not wired.

---

## 7. DPDP erasure

`DpdpService.erase()` runs four steps in order: ownership check → `soft_delete`
(Layer 1) → `memory.forget` (Layer 2) → `audit.redact_patient(doctor_id,
patient_id)`.

---

## 8. API safety invariants

- On every **user-authenticated** route (doctor or patient JWT), `doctor_id` and
  `patient_id` come from the token, never from the body. The one exception is
  `/internal/run-question` (service principal, §5).
- A wrong-tenant request gets a generic **404** (`not found`), never a 403.
- Login failures get one generic **401**, so a response never reveals which ids
  exist.
- No response DTO contains `pin_hmac` or raw secrets.
- Every `422` validation error is reduced to a list of `{type, loc, msg}`: the
  submitted `input` and `ctx` are dropped, so a PIN or password is never echoed
  back. This applies to every route; the web client reads that list shape.
- An unhandled error returns **500** with no traceback in the body.
  `ReasonerUnavailable` maps to **503** where it escapes the graph.
- Spend routes (`/demo/ask`, `/internal/run-question`, `/patient/ask`, and
  `POST /consultations/{id}/extract`, which calls the LLM extractor when a key is set) count
  toward the per-IP minute limit and the daily cap. Client IP is the rightmost
  trusted `X-Forwarded-For` hop (`CARELINE_TRUSTED_PROXY_HOPS`), never the
  client-supplied leftmost entry.
