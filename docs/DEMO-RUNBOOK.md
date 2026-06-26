# CareLine — Demo-Day Runbook & Pre-Flight Checklist

> Viva format: **10 min presentation + 5 min Q&A**. This runbook is the single source
> of truth for getting the demo on screen. Quote it literally — real commands, real
> patient IDs/PINs, real ports.

**Key facts for the day**
- Backend API: `http://localhost:8000` (FastAPI, `careline.combined:app`)
- Frontend: `http://localhost:3000` (Next.js, `next dev`)
- Seed doctor: **`dr-asha`** (web doctor login default) · Mongo tenant `dr-asha`
- Seed patient for the demo: **`ravi-kumar`** · **PIN `1234`** (all seeded patients use PIN `1234`)
- LangSmith project: **`careline`** (endpoint `https://apac.api.smith.langchain.com`)
- Reasoner backend is chosen by **`CARELINE_LLM_BACKEND`** (`heuristic` | `openai` | `anthropic`). Default keyless = `heuristic`.

---

## 1. Pre-flight checklist (morning of)

Tick every box before the room fills up. Each is a literal action.

- [ ] **Pull latest** — `cd careline && git pull --ff-only`
- [ ] **Venv active** — `cd backend && source .venv/bin/activate` (prompt shows `(.venv)`)
- [ ] **Dev extras installed** — `pip install -e ".[dev]"` (re-run; must end clean)
- [ ] **`.env` present and complete** — `backend/.env` has `OPENAI_API_KEY`, `CARELINE_MONGO_URI`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=careline`, `LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com`
- [ ] **Pick the live brain** — for a real LLM demo, leave `CARELINE_LLM_BACKEND` unset-or-`openai` so the spine uses OpenAI (it prefers OpenAI when `OPENAI_API_KEY` is present)
- [ ] **Mongo Atlas reachable** — `python -c "import asyncio; from careline.adapters.mongo import create_client; from careline.config import get_settings; c=create_client(get_settings().mongo_uri); print(asyncio.get_event_loop().run_until_complete(c.admin.command('ping')))"` prints `{'ok': 1.0}` (or just run the seed below — it fails fast if Atlas is unreachable)
- [ ] **Seed data loaded** — `python -m scripts.seed_demo` → prints `Done. 5 patients, 33 facts under 'dr-asha' (PIN 1234).` This also **wipes the audit trail** (old questions/replies) for a clean slate — **restart the backend afterwards** so its in-memory history re-hydrates empty.
- [ ] **Backend boots** — `uvicorn careline.combined:app --factory --reload` → `Application startup complete` on `:8000`
- [ ] **Frontend boots** — `cd web && npm run dev` → ready on `http://localhost:3000`
- [ ] **Smoke question returns ANSWER** — `curl -s localhost:8000/demo/ask -H 'content-type: application/json' -d '{"question":"what is my paracetamol dose?"}' | python -m json.tool` shows `"verdict": "answer"` with a citation
- [ ] **LangSmith shows a trace** — open the **`careline`** project in LangSmith; the smoke question above appears as a new span tree within ~10s
- [ ] **Frontend loads** — open `http://localhost:3000`, console/dashboard render without errors
- [ ] **Patient login works** — go to `http://localhost:3000/patient/login`, sign in as **`ravi-kumar`** / PIN **`1234`** → care portal loads with the paracetamol/soft-diet/penicillin facts
- [ ] **Doctor login works** — `http://localhost:3000/login` as **`dr-asha`**; `/escalations` and `/eval` open
- [ ] **Backup screenshot saved** — a pre-captured LangSmith trace image is on the laptop in case the network drops (see §5)

---

## 2. Two-terminal launch (exact commands)

**Terminal A — backend (port 8000)**
```bash
cd careline/backend
source .venv/bin/activate
python -m scripts.seed_demo          # one-time per session; idempotent, resets dr-asha tenant
uvicorn careline.combined:app --factory --reload
```

**Terminal B — frontend (port 3000)**
```bash
cd careline/web
npm install                          # first run only
npm run dev                          # http://localhost:3000
```

Frontend talks to the backend via `NEXT_PUBLIC_API_BASE` (defaults to `http://localhost:8000`, set in `web/.env.local`).

> **Resetting between practice runs:** `python -m scripts.seed_demo` re-wipes everything (facts + the whole audit trail) and reseeds — restart the backend after. For a lighter reset *during* a session, use the **Clear** button (patient portal header, and the Live Console) to empty just the on-screen conversation/history. The patient Clear is durable (calls `DELETE /patient/history`); the console Clear is the current call only.

---

## 2.5. Seed-data reference (5 patients, all PIN `1234`, under `dr-asha`)

Each patient is a coherent post-consultation record — use them to practise a real spread of verdicts. The Live Console **suggestion chips are now record-driven**: pick a patient and the starter questions reflect *their* facts.

| Patient | Record (current facts) | Good demo questions → expected |
|---|---|---|
| **ravi-kumar** ⭐ | Post-appendectomy; Paracetamol 500mg; **superseded** Amoxicillin; soft-diet + wound-care instructions; penicillin allergy; temp obs; 2-wk follow-up | "what is my paracetamol dose?" → **ANSWER** · "how do I care for my wound?" → **ANSWER** · "am I allergic to anything?" → **ANSWER (penicillin)** · "should I take amoxicillin?" → **ESCALATE** (superseded, never answer from stale) · "can I eat sweets post-surgery given my diabetes?" → **ESCALATE** (cross-condition) |
| **meera-shah** | Type-2 diabetes; Metformin + Glimepiride; low-sugar diet; HbA1c 7.8%; endo follow-up | "what is my metformin dose?" → **ANSWER** · "what's my latest HbA1c?" → **ANSWER** · "when is my endocrinology review?" → **ANSWER** |
| **arjun-nair** | Hypertension + hyperlipidaemia; Atorvastatin + Aspirin + Amlodipine; low-salt diet; BP 138/88; cardiology follow-up | "what blood pressure medicine am I on?" → **ANSWER** · "what's my blood pressure?" → **ANSWER** · "when is my cardiology review?" → **ANSWER** |
| **priya-iyer** | Knee osteoarthritis; Ibuprofen PRN; physiotherapy; BP 130/85; ortho follow-up | "how often can I take ibuprofen?" → **ANSWER** · "what exercises should I do?" → **ANSWER** |
| **sanjay-rao** | Mild asthma; Salbutamol + Budesonide inhalers; avoid smoke/dust; dust-mite allergy; pulmonology follow-up | "when do I use my blue inhaler?" → **ANSWER** · "what triggers my asthma?" → **ANSWER** |

Red-flag rail works for **any** patient: "I have chest pain" → **ESCALATE** at triage, pre-LLM.

---

## 3. The 8-minute live demo script

Maps to the 10-minute presentation timeline. Budget: ~2 min framing, ~4 min live, ~2 min eval/guardrails/contributions. Keep both browser tabs (patient `:3000/patient`, doctor `:3000/escalations`) and a LangSmith tab pre-opened.

| Time | Beat | What you say / do |
|------|------|-------------------|
| 0:00–1:00 | **Problem** | Post-consult, patients call back with follow-ups. A generic chatbot will confidently answer from stale or cross-condition context — clinically dangerous. The rule: *uncertainty always resolves toward ESCALATE; never answer from a superseded fact; one patient per call.* |
| 1:00–2:00 | **Why multi-agent** | No single prompt can be both helpful and fail-closed. We split the job across 7 specialist agents so a **deterministic safety spine** (triage → retrieve → reason → verify → gate) can veto the LLM. The LLM proposes; the gates dispose. |
| 2:00–2:30 | **Architecture** | Show the `START → triage → retrieve → reason → verify → gate → {answer\|clarify\|escalate}` LangGraph. Two-layer data: Mongo source-of-truth (temporal validity + doctor approval) + a RAG memory layer. Brain is the single safety authority; a parity test proves the graph never disagrees with it. |
| 2:30–6:30 | **Live demo** | Run (a)–(e) below in order. |
| 6:30–7:30 | **Eval / guardrails** | Run (f): `/eval` T1–T8 live. |
| 7:30–8:00 | **Contributions** | 7 agents, 5 owners (Ruthwik orchestration/graph, Vinay rails+gates, Naga RAG+Mongo, Srujan reasoner+verifier, Naresh extraction+API). |

### Live-demo exact steps

**(a) Happy path — ANSWER with citation + trace**
1. Patient tab → `http://localhost:3000/patient` (already signed in as `ravi-kumar`).
2. In *Ask a follow-up question*, type exactly: **`what is my paracetamol dose?`** → press **Ask**.
3. Expect an **ANSWER**: "Paracetamol 500mg twice daily for post-op pain" with a citation back to fact `ravi-med-1`.
4. (Optional, richer trace) On the doctor **Live Console** (`/console`), run the same question and open the **trace stepper** — show triage → retrieve → reason → verify → gate each green.

**(b) Safety save — cross-condition ESCALATE, no LLM call**
1. Still in the patient portal, type exactly: **`can I eat sweets post-surgery given my diabetes?`** → **Ask**.
2. Expect **ESCALATE** ("I've passed this to your doctor to answer safely…"). Point out: this is **T3 cross-condition** — it escalates **at triage, pre-LLM**, so no model token is ever spent merging guidance across conditions.

**(c) Greeting handled — friendly CLARIFY, not escalation**
1. Type exactly: **`good morning`** → **Ask**.
2. Expect a **CLARIFY** / friendly redirect ("Could you share a bit more detail?") — the conversational rail handles small-talk gracefully instead of paging the doctor. Distinguishes *can't-answer* from *must-escalate*.

**(d) HITL loop — doctor replies, patient sees it**
1. Switch to the **doctor** tab → `http://localhost:3000/escalations` (logged in as `dr-asha`). The cross-condition turn from (b) is waiting.
2. Open it, type a short reply (e.g. "Limit sweets — your metformin... let's review at your follow-up."), **send**.
3. Switch back to the **patient** tab → `http://localhost:3000/patient`. Under *Your questions & answers*, the escalated turn now shows **"Your doctor replied"** with the doctor's text. (Refresh if needed — the portal reloads question history on each ask.)

**(e) Observability — LangSmith live span tree**
1. Switch to the LangSmith tab → **`careline`** project.
2. Open the most recent trace = the happy-path question from (a). Walk the span tree: triage → retrieve → reason → verify → gate, with latency and the gate verdict on the root. This is the proof the run is real, not scripted.

**(f) Eval — T1–T8 live results**
1. Doctor tab → `http://localhost:3000/eval`.
2. The backend **reruns all eight scenarios through the live gate chain on every load**; each row shows its freshly-computed verdict + pass/fail. Call out:
   - **T1** discontinued-med recall → ESCALATE
   - **T3** cross-condition → ESCALATE (the (b) save, now as a regression test)
   - **T5** in-scope happy path → ANSWER (the (a) path, proving we don't over-escalate)
   - **T6** cross-patient isolation → ESCALATE on empty slice (sev-0 guard)

---

## 4. Backup plan (network / key failure)

If Mongo Atlas, OpenAI, or LangSmith is unreachable, run **fully offline and keyless** — the M0 brain needs no API key and no database.

```bash
# Terminal A — backend, offline
cd careline/backend
source .venv/bin/activate
CARELINE_LLM_BACKEND=heuristic CARELINE_MONGO_URI="" \
  uvicorn careline.combined:app --factory --reload
```

- `CARELINE_LLM_BACKEND=heuristic` → the offline heuristic reasoner/verifier twins (no OpenAI). Safe to demo locally; the factory only *refuses* heuristic when `CARELINE_ENV` is `prod`/`production`.
- `CARELINE_MONGO_URI=""` → falls back to the bundled in-memory demo patient; the keyless `/demo/ask` happy path (`what is my paracetamol dose?` → ANSWER) and the escalation/greeting cases still work.
- Frontend Live Console + `/eval` run offline against `/demo/*` — T1–T8 are designed keyless.
- **If LangSmith is down:** show the **pre-saved screenshot** of a `careline` trace (captured during pre-flight) instead of the live tab. Narrate the same span tree.
- Note: in offline mode you cannot seed Mongo (`seed_demo` requires `CARELINE_MONGO_URI`), so the *patient-portal login* path is unavailable — demo (a)/(b)/(c) via the keyless **Live Console** instead, and skip the HITL persistence step (d).

---

## 5. Likely Q&A landmines (and one-line answers)

- **"Does the agent web-search / use external knowledge?"** — No, by design. It answers *only* from the doctor's approved, currently-valid facts for that one patient. Anything outside that slice escalates — no open-web tool exists.
- **"Isn't this just RAG?"** — RAG is *retrieval over the valid slice* (memory proposes); the Mongo source-of-truth then **re-validates** every retrieved fact against half-open temporal validity + approval before it can support an answer (source-of-truth disposes). Memory can suggest a stale fact; Layer 1 will reject it.
- **"What if the LLM hallucinates a verdict?"** — It can't decide the verdict. The **gatekeeper is deterministic** — confidence + risk gates, independent verifier veto. The LLM only proposes a candidate answer; the gates choose ANSWER/CLARIFY/ESCALATE. A parity test proves the graph never deviates from the headless Brain.
- **"What happens on an error or unknown?"** — **Fail closed.** Any uncertainty, missing context, or unavailable dependency resolves to **ESCALATE** to the doctor — never a guess. A cross-patient leak is treated as sev-0 and is structurally impossible (one patient per call).
