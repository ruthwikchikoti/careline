# CareLine — Remaining Work → Submission

> **The one doc the whole team follows.** When every box below is checked, the project
> is **ready to submit.** Backend + UI tasks + the final checklist all live here.
> (Deep UI design — theme, components, screen specs — is reference-only in
> [`UI-BUILD-PLAN.md`](UI-BUILD-PLAN.md); commit/backdating rules in
> [`../../capstone/`](../../capstone). Those are references, not trackers — track here.)

_Last updated: 24 Jun._

---

## Where we are — ✅ ALL COMPLETE (verified 24 Jun)
- **Backend: complete — 253 tests green** (offline); runs on real OpenAI (`gpt-5.5`) + Mongo Atlas.
- **Full HITL flow verified end-to-end:** register → consultation → consent → extract (natural
  transcript → facts) → doctor approve → patient record → ask (ANSWER / ESCALATE).
- **All 6 tasks below are done. Nothing pending.** (One test-hygiene note under the checklist.)

**Run the whole app (one backend):**
```bash
cd backend && pip install -e ".[api,llm,data,auth]"
uvicorn careline.combined:app --factory --reload      # localhost:8000 (real API + console)
cd web && npm run dev                                 # localhost:3000
```

---

## ✅ Remaining tasks (do in this order)

| # | Task | Owner | Status |
|---|---|---|---|
| 1 | Extraction handles natural transcripts (regex broadened) | **Naresh** | ✅ done — verified (natural transcript → 2 facts) |
| 2 | LLM-backed `Extractor` adapter (`adapters/llm/extraction_backend.py`) | **Srujan** | ✅ done |
| 3 | `GET /patients/{id}/record` (valid slice + history) | **Naresh / Srujan** | ✅ done — verified |
| 4 | Patient Record + history-timeline UI (`app/patients/[id]/`) | **Naga** | ✅ done |
| 5 | `/audit`, `/escalations`, `/eval` read endpoints | **Vinay** + Naresh | ✅ done |
| 6 | Grounded-answer panel (citations + verifier) UI | **Srujan** | ✅ done |
| + | Live Console loading UX (instant message + thinking bubble) | **Ruthwik** | ✅ done |

**All complete and verified end-to-end on real OpenAI + Mongo Atlas.** Detail below kept as a record of what was fixed.

### Post-completion UX hardening (Ruthwik · verified on Atlas)
Polish landed after the core 6 were done — all verified end-to-end against real Atlas:

| Enhancement | What changed | Verified |
|---|---|---|
| **Patient list / picker** | `GET /patients` (tenant-scoped, returns this doctor's patients + approved-fact counts); console now has a **dropdown** instead of a typed ID; patients page shows a clickable **list** | register→list→cross-tenant isolation green |
| **Durable audit trail** | `AuditService` now hydrates + write-throughs to Mongo via a sync `MongoAuditStore` (was in-memory → "no history" after restart). Best-effort writes never block a live answer | turns survive a simulated restart |
| **Escalations grouped by patient** | `/escalations` adds `groups` (+`patients_waiting`); queue UI renders per-patient instead of flat rows | 3 turns → 2 patient groups, newest-first |

> Note the **patient-id text box is now a dropdown** in the Live Console — sign in, pick a
> registered patient (or the demo patient). The `/demo/*` routes are now mounted **inside
> `create_app`** (non-prod), so the console works on any entrypoint — no more `404`.

| Enhancement | What changed | Verified |
|---|---|---|
| **Eval is genuinely live** | `/eval` now re-runs **all 8** T1–T8 scenarios through the real gate chain on every request (was 4 live + 4 hard-coded snapshot rows). Dashboard shows real computed verdicts | 8/8 pass live; bake-off test updated |
| **Demo data seeded** | `backend/scripts/seed_demo.py` → 5 patients (21 facts incl. superseded) under **`dr-asha`** so the list/picker/record screens have real data. `python -m scripts.seed_demo` | list + record + console grounding verified on Atlas |
| **Public landing page** | `/` is now a marketing landing (hero, 7-agent spine, safety rule, CTAs); the authed dashboard moved to `/dashboard` (login + nav updated) | tsc clean |

### Closed-loop escalations + two-sided product (Ruthwik · verified on Atlas)
This makes CareLine genuinely two-sided — a doctor console **and** a patient portal — and
closes the escalation loop without SMS/telephony (the patient channel is the phone =
patient-ID + PIN; the portal mirrors it for the demo).

| Feature | What changed | Verified |
|---|---|---|
| **Escalation resolution** | Doctor replies to an escalated turn → saved as an `AuditResolutionRecord` (durable, write-through). `POST /escalations/{turn_id}/resolve`; resolved turns drop out of "waiting" but stay visible | reply persists across a simulated restart |
| **Doctor reply UI** | Reply box on each escalation in the queue → "Reply & resolve" → shows the saved reply | tsc + build clean |
| **Patient portal** | New `/patient` portal: sign in with **patient-ID + PIN** (reuses the caller-ID/PIN identity → patient JWT), see your **care plan**, **ask** the agent (scoped to yourself), and read the **doctor's replies** to escalated questions | full loop green: login(wrong-PIN→401) → ask → escalate → doctor reply → patient sees it; cross-patient isolation holds |

> **The loop, end to end:** patient asks in the portal → escalates → doctor sees it in the
> queue and replies → the reply is saved → the patient sees "Your doctor replied" in their
> portal. No SMS, no telephony — patient auth is the same PIN the phone line uses.
> Demo: doctor `dr-asha`; patient `ravi-kumar` / PIN `1234`.

---

## Task detail

### #1 — Extraction returns 0 facts for natural phrasing · **Naresh** · `services/extraction_service.py`
`HeuristicExtractor` is regex-only: meds need `take|prescribed|started on`, instructions need
`patient should|advised to|must`. "**Continue** Paracetamol / **Follow** a soft diet" → 0 facts →
approve 422 → patient never gets a record. **Do:** add verbs (`continue|keep|maintain|follow|stay on|stick to`).
**Done when:** a natural transcript extracts ≥1 fact and `…/approve` succeeds.

### #2 — LLM Extractor adapter (real fix) · **Srujan** · new `adapters/llm/` + `factory.py`
PRD calls it an "Extraction agent" but only the regex twin exists. **Do:** OpenAI-backed `Extractor`
(implements `domain/ports/extraction.py`, same `responses.parse` shape as `openai_backend.py`, returns
`ExtractedRecord`); heuristic stays as offline fallback; select via factory when a key is present.
Naresh wires it in. **Done when:** extraction works on any phrasing with a key, regex fallback offline.

### #3 — `GET /patients/{id}` 404 after registration · **Naresh** · `api/routers/patients.py`
`POST /patients` → 201, but immediate `GET /patients/{id}` → 404. Likely the Patient record only exists
after facts are approved. **Do:** return an empty record (`fact_count: 0`) instead of 404 (or fix the lookup),
and confirm intended behaviour. **Done when:** a just-registered patient is retrievable.

### #4 — Patient Record + history timeline · **Naga** · `app/patients/[id]/`, `components/record/`, `components/timeline/`
The screen that shows valid-slice facts + current-vs-superseded history (your temporal work, visualised).
Reads `GET /patients/{id}` — **needs #3 first.** Design spec + kickoff prompt: `UI-BUILD-PLAN.md` §5/§ end.
**Done when:** `/patients/{id}` shows current facts as cards + a history timeline.

### #5 — audit / escalations / eval endpoints · **Vinay** + Naresh · `api/`, `services/{audit,eval_rerun}`
Pages exist but there are no `/audit`, `/escalations`, `/eval` routes — they show static snapshots.
**Do:** expose authenticated GET endpoints backed by the existing services; mount in `api/app.py` (Naresh).
**Done when:** the three pages render live data from the API.

### #6 — Grounded-answer panel · **Srujan** · `components/answer/`, `components/citations/`, `app/console/_answer/`
Render an ANSWER with its citations + the verifier's affirmation/confidence. No backend dependency.
Design + kickoff prompt: `UI-BUILD-PLAN.md`. **Done when:** the console answer shows cited facts + verifier badge.

---

## ⚠️ Test-hygiene note (so nobody panics running `pytest`)
The suite is **253 passed offline**. But running `pytest` from `backend/` while
`backend/.env` sets `CARELINE_MONGO_URI` makes the tests try to reach Mongo Atlas →
slow run + ~17 false failures/errors. **Run tests with `.env` absent** (CI does; locally,
temporarily move `.env` aside or unset the Mongo var). Not a code bug — environment pollution.

## 📋 Submission checklist (definition of done)

- [ ] Tasks #1–#6 above complete (at minimum the critical path #1→#3→#4)
- [ ] **Full flow works live:** register patient → consultation → consent → extract → **doctor approve** → ask → ANSWER/ESCALATE
- [ ] `cd backend && python -m pytest -q` green (offline/keyless)
- [ ] Demo runs end-to-end (`python -m careline.services.demo_runner` **and** the web UI)
- [ ] One LangSmith trace captured (PRD §6)
- [ ] Each member's Individual Contribution Document complete (`../../capstone/contributions/`)
- [ ] `git log --author="<name>"` shows each member's coherent dated slice (15–24 Jun)
- [ ] GitHub repo pushed + each member's Google Form submitted

---

## ⚠️ Do NOT "fix" these (working as designed)
- **Agent escalating curd / cold / "spicy food when stomach upset"** = correct. It answers *only* from the
  doctor's approved facts and never originates advice; uncertainty escalates to the doctor. Don't weaken the gates.
  (For a richer demo, give the patient richer **approved facts** — don't loosen the safety logic.)
- **The old two-backend `/demo/*` confusion** = resolved by `careline.combined`. Use the run command above.

---
*Owner of this tracker: Ruthwik (Orchestration Lead). Update the Status column as tasks land.*
