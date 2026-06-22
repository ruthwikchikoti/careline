# INTERFACE-CONTRACTS.md — Frozen Cross-Member Interfaces

> **Owner:** Naresh (scope `api`). Sign-off from interface owners before changing
> any field listed here. The drift-guard tests in
> `tests/brain/test_dpdp_service.py::TestInterfaceDriftGuard` enforce these shapes.

Uncertainty always resolves toward **ESCALATE**. Never answer from a superseded
fact. One patient per call — zero cross-patient reachability.

---

## 1. Layer-1 source of truth (Naga)

### `ValidSlice` — `domain/model/patient.py`

| Field | Type | Notes |
|---|---|---|
| `as_of` | `datetime` | Instant the slice was computed |
| `facts` | `tuple[Fact, ...]` | Approved, currently-valid facts only |

### `PatientRepository` — `domain/ports/repositories.py`

Every method requires keyword-only `doctor_id`. Cross-tenant reads return `None`.

| Method | Returns | Purpose |
|---|---|---|
| `get` | `Patient \| None` | Full aggregate |
| `exists` | `bool` | Tenant-scoped existence |
| `valid_slice` | `ValidSlice` | Grounding context for reasoning |
| `history` | `tuple[Fact, ...]` | Retired facts for audit |
| `add_facts` | `None` | Append without supersession |
| `apply_facts` | `tuple[Fact, ...]` | §B.6 supersession write path |
| `soft_delete` | `int` | DPDP erasure — null clinical text, keep skeleton |
| `find_by_caller` | `PatientIdentity \| None` | Caller-id lookup |
| `upsert_identity` | `None` | Register caller-id / pin_hmac |

---

## 2. Layer-2 memory / RAG (Naga)

### `MemoryProvider` — `domain/ports/memory.py`

Namespace is always `(doctor_id, patient_id)` — no cross-patient retrieval path.

| Method | Purpose |
|---|---|
| `index` | Rebuild retrieval namespace from approved valid slice |
| `retrieve` | Return up to `k` relevance hits for one patient |
| `forget` | Drop entire namespace (DPDP erasure on Layer 2) |

### `MemoryHit`

| Field | Type |
|---|---|
| `fact_id` | `str` |
| `text` | `str` |
| `score` | `float` |
| `kind` | `FactKind \| None` |

---

## 3. Reasoning ports (Srujan)

### `Reasoner` / `Verifier` — `domain/ports/reasoning.py`

| Port | Method | Input | Output |
|---|---|---|---|
| `Reasoner` | `propose` | `question`, `context: ValidSlice` | `ClassifierProposal` |
| `Verifier` | `verify` | `question`, `context`, `proposal` | `VerificationResult` |

**Fail-closed:** implementations MUST raise `ReasonerUnavailable` rather than
return a guess. The API maps this to HTTP 503.

---

## 4. Decision handoff (Ruthwik / Vinay)

### `Decision` — `domain/model/decision.py`

| Field | Type | Notes |
|---|---|---|
| `verdict` | `Verdict` | `answer` / `clarify` / `escalate` |
| `answer_text` | `str \| None` | Answer or clarify prompt |
| `escalation_reason` | `str \| None` | Required on ESCALATE |
| `scope` | `ScopeCategory \| None` | |
| `confidence` | `float` | `[0, 1]` |
| `risk` | `float` | `[0, 1]` |
| `citations` | `list[str]` | Fact ids supporting the answer |
| `trace` | `ReasoningTrace` | Explainable pipeline steps |

Construct only via `Decision.answer()`, `.clarify()`, `.escalate()`.

### `ReasoningTrace` / `TraceStep`

| TraceStep field | Type |
|---|---|
| `name` | `str` |
| `status` | `TraceStatus` |
| `spec_section` | `str \| None` |
| `detail` | `str \| None` |

---

## 5. Internal brain endpoint (Naresh ↔ Vinay)

### `POST /internal/run-question`

**Auth:** `X-Internal-Key` header (internal principal).

### `QuestionIn` — `api/dto/brain.py`

| Field | Type |
|---|---|
| `doctor_id` | `str` |
| `patient_id` | `str` |
| `call_id` | `str` |
| `question` | `str` |

### `AnswerOut` — `api/dto/brain.py`

| Field | Type |
|---|---|
| `verdict` | `Verdict` |
| `answer_text` | `str \| None` |
| `escalation_reason` | `str \| None` |
| `confidence` | `float` |
| `risk` | `float` |
| `citations` | `list[str]` |
| `trace` | `list[TraceStepOut]` |

Vinay's `QuestionService.run_question()` is the application entry behind this route.

---

## 6. Telephony escalation sink (Vinay)

### `EscalationPayload` — `adapters/telephony/stub.py`

| Field | Type |
|---|---|
| `call_id` | `str` |
| `patient_id` | `str` |
| `doctor_id` | `str` |
| `reason` | `str` |
| `escalated_at` | `datetime` |
| `terminal_gate` | `str \| None` |

### `TelephonyPort`

| Method | Purpose |
|---|---|
| `escalate(payload)` | Initiate live transfer to the doctor |

---

## 7. DPDP erasure (Naresh)

### `DELETE /patients/{patient_id}/data`

**Auth:** Bearer JWT (`DoctorPrincipal`). `doctor_id` from principal only.

### `ErasureOut` — `api/dto/patients.py`

| Field | Type |
|---|---|
| `patient_id` | `str` |
| `layer1_nulled` | `int` |
| `layer2_dropped` | `bool` |
| `audit_redacted` | `int` |

`DpdpService.erase()` orchestrates: ownership check → `soft_delete` →
`memory.forget` → `audit.redact_patient`.

---

## 8. API safety invariants (all routes)

- `doctor_id` is **never** trusted from request bodies — always from the
  authenticated principal.
- Wrong-tenant requests return generic **404** (`not found`), never 403.
- No `pin_hmac` or clinical payloads in any response DTO.
- Unhandled errors return **500** with no traceback leakage.
