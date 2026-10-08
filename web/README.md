# CareLine — Web

The Next.js frontend for CareLine: the doctor's workspace (dashboard, Live Agent
Console, escalations + review queue, audit, eval, patients, consultations) and the
patient's own portal. Every screen reads the FastAPI backend in `../backend`; the
safety decisions are made there, never in the browser.

## Run it (two terminals)

**1. Backend** — the full API (doctor login, portal, audit), offline/keyless by default:
```bash
cd ../backend
pip install -e ".[api]"
CARELINE_DOCTOR_PASSWORD=<choose-one> uvicorn careline.api.app:create_app --factory --reload   # :8000
```
For the anonymous console only, `uvicorn careline.demo_server:app --reload` is enough.

**2. Web:**
```bash
npm install
npm run dev                                     # http://localhost:3000
```

Open http://localhost:3000 → Dashboard → **Start a call** → ask a question. Keyless,
against the bundled demo patient:

| Question | Verdict |
|---|---|
| `soft diet post surgery` | ANSWER (cites the current diet instruction) |
| `should I take amoxicillin` | CLARIFY or ESCALATE — the medicine was discontinued, so it is never answered |
| `Can I eat sweets post-surgery given my diabetes?` | ESCALATE — cross-condition |
| `I have chest pain` | ESCALATE — red-flag rail, before any LLM |

## Screens

- `app/dashboard/`, `app/console/` — overview and the Live Agent Console (the five
  graph nodes: triage → retrieve → reason → verify → gate, then answer / clarify /
  escalate, with the real trace).
- `app/escalations/` — the doctor's handoff queue, grouped by patient, plus a
  **Redirected — please review** list: CLARIFY turns whose question reads like a
  symptom report. Those did not page the doctor, so a human reviews and can reply
  to them exactly like an escalation.
- `app/audit/`, `app/eval/` — tenant-scoped audit trail and the T1–T8 re-run
  (production thresholds).
- `app/patients/`, `app/consultations/` — patient registration (6-digit numeric
  PIN), records with current vs superseded facts, consultation approval.
- `app/patient/` — the patient portal: care plan, ask a follow-up, read the
  doctor's replies. Every agent reply shows its own text; a red-flag turn shows a
  prominent "call 112 (India) or your local emergency number" banner.

## Code map

- `tailwind.config.ts` — design tokens shared by every screen.
- `components/ui/` — design system (`VerdictPill`, `Card`, `Button`, `Input`).
- `components/trace/TraceStepper.tsx` — the agent-trace visualizer.
- `components/shell/AppShell.tsx` — doctor workspace shell and nav.
- `lib/api.ts` — typed client for the backend (doctor and patient sessions).
