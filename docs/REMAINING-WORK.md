# CareLine — Remaining Work → Submission

> **The one doc the whole team follows.** When every box below is checked, the project
> is **ready to submit.** Backend + UI tasks + the final checklist all live here.
> (Deep UI design — theme, components, screen specs — is reference-only in
> [`UI-BUILD-PLAN.md`](UI-BUILD-PLAN.md); commit/backdating rules in
> [`../../capstone/`](../../capstone). Those are references, not trackers — track here.)

_Last updated: 24 Jun._

---

## Where we are
- **Backend: complete.** 230 tests green; runs on real OpenAI (`gpt-5.5`) + Mongo Atlas.
- **Ruthwik's slice: complete** (graph, brain, Decision, parity, Live Console, OpenAI-primary wiring, combined entrypoint).
- **What's left:** 4 small code fixes + 2 UI screens. All below, in order.

**Run the whole app (one backend):**
```bash
cd backend && pip install -e ".[api,llm,data,auth]"
uvicorn careline.combined:app --factory --reload      # localhost:8000 (real API + console)
cd web && npm run dev                                 # localhost:3000
```

---

## ✅ Remaining tasks (do in this order)

| # | Task | Owner | Pri | Status | Blocks |
|---|---|---|---|---|---|
| 1 | Fix extraction so natural transcripts yield facts (broaden regex) | **Naresh** | 🔴 High | ⬜ | the whole consult→approve→answer demo |
| 2 | LLM-backed `Extractor` adapter (real fix for #1) | **Srujan** | 🔴 High | ⬜ | — (supersedes #1) |
| 3 | `GET /patients/{id}` returns the record (not 404) after register | **Naresh** | 🟡 Med | ⬜ | task #4 |
| 4 | Patient Record + history-timeline UI page | **Naga** | 🟡 Med | ⬜ | needs #3 |
| 5 | Backend read endpoints for audit / escalations / eval | **Vinay** + Naresh | 🟡 Med | ⬜ | makes those pages live |
| 6 | Grounded-answer panel (citations + verifier) UI | **Srujan** | 🟢 Low | ⬜ | — (polish) |

**Critical path to a working end-to-end demo: #1 → #3 → #4.** #2/#5/#6 are parallel.
If time is tight, just do **#1** and you have a complete clickable flow.

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
