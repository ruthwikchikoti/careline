# CareLine — Web (Live Agent Console)

The frontend for CareLine. This **foundation slice** (owner: Ruthwik) ships the design
system, the app shell, and the **Live Agent Console** — type a patient question and
watch it route to ANSWER / CLARIFY / ESCALATE with the real agent trace. Other screens
are split per [`../docs/UI-BUILD-PLAN.md`](../docs/UI-BUILD-PLAN.md).

## Run it (two terminals)

**1. Backend demo API** (zero setup, offline/keyless):
```bash
cd ../backend
pip install -e ".[api]"
uvicorn careline.demo_server:app --reload      # http://localhost:8000
```

**2. Web:**
```bash
npm install
npm run dev                                     # http://localhost:3000
```

Open http://localhost:3000 → Dashboard → **Start a call** → ask a question.
Try: `soft diet post surgery` (ANSWER), `should I take amoxicillin` (ESCALATE — discontinued),
`Can I eat sweets post-surgery given my diabetes?` (ESCALATE — cross-condition),
`I have chest pain` (ESCALATE — red-flag, pre-LLM).

## What's here (foundation)
- `tailwind.config.ts` — the shared design tokens (§4.2 of the plan).
- `components/ui/` — design system: `VerdictPill`, `Card`, `Button`.
- `components/trace/TraceStepper.tsx` — the agent-trace visualizer.
- `components/shell/AppShell.tsx` — doctor workspace shell + nav (shows each screen's owner).
- `app/console/` — the Live Agent Console.
- `lib/api.ts` — typed client for the demo backend.

The console talks to `careline.demo_server`, which runs the **real** multi-node graph
against a bundled demo patient — no auth, no database. Production screens use the
authenticated `/internal/run-question` API.
