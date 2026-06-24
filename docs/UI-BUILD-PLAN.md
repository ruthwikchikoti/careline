# CareLine — Frontend / UI Build Plan

> A web app that makes the 7-agent safety spine **visible**: a doctor manages a
> patient's approved record, and a patient "calls in" to ask follow-up questions
> that route live to **ANSWER / CLARIFY / ESCALATE** with the agent trace on screen.
> One shared design system, split into five member-owned slices — same model as the
> backend.

**Read with:** [`architecture.md`](architecture.md) (the graph this UI visualizes) ·
the backend API (`backend/careline/api/`).

---

## 1. Goal & principles

- **Show the IP, don't hide it.** The showpiece is the *Live Agent Console*: ask a
  question → watch `triage → retrieve → reason → verify → gate` light up and route to
  a verdict, with the reasoning trace rendered as a stepper. This is what makes the
  multi-agent system legible to a viewer in 10 seconds.
- **Healthcare-grade trust.** Calm, clinical, accessible (WCAG AA contrast). Safety
  states are unmistakable: ANSWER = green, CLARIFY = amber, ESCALATE = red.
- **One cohesive product, five builders.** A shared theme + component library means
  five people build different screens that still look like one app.
- **Talks to the real backend.** No mock data — every screen calls the existing
  FastAPI endpoints. Offline/keyless still works (heuristic twins).

## 2. Tech stack (decided)

| Concern | Choice | Why |
|---|---|---|
| Framework | **Next.js (App Router) + TypeScript** | file-based routes = clean per-member split; SSR-ready |
| Styling | **Tailwind CSS** | shared tokens, no CSS collisions between members |
| Components | **shadcn/ui** (Radix + Tailwind) | accessible primitives, consistent look, easy to theme |
| Icons | **lucide-react** | one icon set |
| Animation | **framer-motion** | the trace stepper / verdict reveal |
| Data fetching | **TanStack Query** + a typed `lib/api` client | caching, loading/error states for free |
| Charts (eval) | **Recharts** | T1–T8 dashboard |

Backend stays as-is (FastAPI). The frontend lives in a new top-level **`web/`** folder
in this repo, talking to the API at `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`).

## 3. The product flow → screens

The exact CareLine journey, two personas (Doctor = buyer, Patient = caller):

```
DOCTOR SIDE                                   PATIENT SIDE
─────────────────────────────                 ─────────────────────────────
Login ─► Dashboard ─► Register patient         "Call in" ─► Live Agent Console
   │         │                                      │
   ▼         ▼                                       ▼  ask a question
Patient Record (valid slice + history)         triage→retrieve→reason→verify→gate
   │                                                │
   ▼  new consultation                              ▼
Submit transcript ─► Extraction drafts facts    ANSWER  (grounded, with citations)
   │                                            CLARIFY (ask follow-up)
   ▼  ONE-TAP APPROVE  (human-in-the-loop)      ESCALATE ─► doctor handoff
Approved facts become live context                  │
                                                    ▼
Escalations queue ◄─────────────────────────── Doctor gets the call
Audit log · Eval (T1–T8) · "Delete my data" (DPDP)
```

Screen list: **Login · Doctor Dashboard · Register Patient · Patient Record ·
Consultation (transcript → extraction → approval) · Live Agent Console · Escalations ·
Audit Log · Eval Dashboard · Privacy/DPDP.**

## 4. Design system / theme (build this first, everyone consumes it)

### 4.1 Brand & tone
Clean clinical SaaS. Lots of white space, soft cards, one calm primary, loud safety
colors only where safety is at stake.

### 4.2 Color tokens (`tailwind.config.ts` → `theme.extend.colors`)
```
primary    #0E7C86   (teal — brand, buttons, active nav)   primary-fg #FFFFFF
surface    #FFFFFF   card background
canvas     #F6F8FA   app background (soft slate)
ink        #0F172A   primary text (slate-900)
muted      #64748B   secondary text (slate-500)
border     #E2E8F0   (slate-200)

# verdict / safety (the only "loud" colors — use ONLY for verdicts & alerts)
answer     #059669   emerald-600   answer-bg   #ECFDF5
clarify    #D97706   amber-600     clarify-bg  #FFFBEB
escalate   #DC2626   red-600       escalate-bg #FEF2F2
redflag    #B91C1C   red-700       (emergency rail)
```
Dark mode optional (Phase 2). Ship light mode first.

### 4.3 Typography
- **Inter** (`next/font`) everywhere. Weights 400/500/600/700.
- Scale: `text-2xl/semibold` page titles · `text-lg/medium` card titles ·
  `text-sm` body · `text-xs/medium uppercase tracking-wide` labels.

### 4.4 Shape & depth
- Radius: cards `rounded-2xl`, controls `rounded-lg`, pills `rounded-full`.
- Shadow: `shadow-sm` resting, `shadow-md` on hover/active.
- Spacing: 4px base; cards `p-6`; section gap `gap-6`.

### 4.5 Core shared components (the contract every screen builds from)
| Component | Props (sketch) | Used by |
|---|---|---|
| `<VerdictPill verdict>` | `answer\|clarify\|escalate` → colored pill + icon | everyone |
| `<TraceStepper steps>` | ordered `{name,status,detail}` → vertical stepper, terminal/skipped styling | console |
| `<FactCard fact>` | med/instruction; shows validity (current vs superseded), `approved_by` | record |
| `<Timeline items>` | temporal lane: current vs historical facts | record |
| `<ChatBubble role text>` | patient/agent bubbles for the console | console |
| `<ApprovalCard fact onApprove>` | drafted fact + one-tap approve | consultation |
| `<StatCard>` `<DataTable>` `<EmptyState>` `<Toast>` | dashboards/lists | many |
| `<AppShell>` | sidebar nav + topbar; wraps doctor screens | all doctor screens |

### 4.6 Layout shell (ASCII)
```
┌──────────────────────────────────────────────────────────┐
│  CareLine ◗            Dr. Asha Rao ▾        [Help] [⏏]    │  topbar
├────────────┬─────────────────────────────────────────────┤
│ ◉ Dashboard│                                              │
│ ◐ Patients │            <page content>                    │
│ ⚑ Escalat. │                                              │
│ 🗎 Audit    │                                              │
│ ▤ Eval     │                                              │
│ ⚙ Privacy  │                                              │
└────────────┴─────────────────────────────────────────────┘
```
The **Live Agent Console** is full-bleed (patient-facing), not inside the shell:
```
┌── Patient: Ravi K. (demo) ───────────────────────────────┐
│  ┌─ conversation ─────────────┐  ┌─ agent trace ───────┐ │
│  │ 🧑 "can I eat curry?"       │  │ ● triage     pass   │ │
│  │ 🤖 [ANSWER pill] Soft diet… │  │ ● retrieve   pass   │ │
│  │ 🧑 "I have chest pain"      │  │ ● reason     pass   │ │
│  │ 🤖 [ESCALATE] transferring… │  │ ● verify     pass   │ │
│  └────────────────────────────┘  │ ● gate    ► ANSWER  │ │
│  [ type a question…        ] [▶] └─────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

## 5. Ownership split (5 members — same scopes idea as the backend)

Each member owns distinct routes/components so `git blame` stays clean. Commit scope in
parentheses. **Ruthwik scaffolds the project + design system first (everyone depends on it).**

| Member | UI slice | Scope | Owned paths (under `web/`) |
|---|---|---|---|
| **Ruthwik** | Project scaffold, **design system + AppShell**, **Live Agent Console** (the showpiece: chat + `TraceStepper` + verdict routing) | `ui-core` | `app/layout.tsx`, `app/console/`, `components/ui/`, `components/trace/`, `lib/theme*`, `tailwind.config.ts` |
| **Naresh** | **Auth/Login**, **Consultation → transcript → extraction → one-tap Approval (HITL)**, shared **API client** | `ui-clinical` | `app/login/`, `app/consultations/`, `app/patients/new/`, `lib/api/`, `components/approval/` |
| **Naga** | **Patient Record**: valid-slice (current meds/instructions) + **history timeline** (current vs superseded) | `ui-record` | `app/patients/[id]/`, `components/record/`, `components/timeline/` |
| **Srujan** | **Grounded-answer panel**: how an ANSWER renders with **citations** + **verifier affirmation** + confidence; reason/verify trace detail cards | `ui-answer` | `components/answer/`, `components/citations/`, `app/console/_answer/` |
| **Vinay** | **Escalations queue**, **Audit log**, **Eval (T1–T8) dashboard**, safety badges (`VerdictPill`, red-flag) | `ui-safety` | `app/escalations/`, `app/audit/`, `app/eval/`, `components/badges/` |

> Each member: build only inside your owned paths; consume Ruthwik's `components/ui` +
> tokens; use Naresh's `lib/api` client (never `fetch` raw). Coordinate on shared files
> (`app/layout.tsx`, nav config) like the backend.

> **Live status of who's built what — and everything left to submit — is tracked in the
> single team doc [`REMAINING-WORK.md`](REMAINING-WORK.md), not here.** This file is
> the UI **design reference** only.

## 6. Screen specs (purpose · key components · API)

| Screen | Owner | Purpose | API call(s) |
|---|---|---|---|
| Login | Naresh | doctor signs in (JWT) | `POST /auth/token` |
| Dashboard | Naresh (shell: Ruthwik) | patients + recent escalations | `GET /patients`, `GET /escalations*` |
| Register Patient | Naresh | add a patient | `POST /patients` |
| Patient Record | Naga | current valid facts + history | `GET /patients/{id}` |
| Consultation | Naresh | transcript → extraction → **approve** | `POST /consultations`, `/{id}/consent`, `/{id}/extract`, `/{id}/approve` |
| **Live Agent Console** | Ruthwik (+Srujan answer panel) | ask → route → trace | `POST /internal/run-question` |
| Escalations | Vinay | doctor handoff queue | audit/escalation reads |
| Audit Log | Vinay | per-call turns + verdicts | audit reads |
| Eval Dashboard | Vinay | T1–T8 pass/fail | static eval JSON / `eval_rerun` |
| Privacy / DPDP | Naresh | "delete my data" | `DELETE /patients/{id}/data` (NR-7) |

**`run-question` response → UI:** render `verdict` as `<VerdictPill>`, `answer_text`
in a `<ChatBubble role="agent">`, and `trace.steps[]` in `<TraceStepper>` (color
`terminal` red/amber, `skipped` greyed, `pass` green). Citations (Srujan) link to the
`FactCard`s on the Record screen.

## 7. Folder structure (`web/`)
```
web/
  app/
    layout.tsx                # Ruthwik — AppShell + fonts + theme
    page.tsx                  # Ruthwik — redirect to dashboard
    login/                    # Naresh
    console/                  # Ruthwik (+ _answer/ Srujan)
    patients/[id]/            # Naga
    patients/new/             # Naresh
    consultations/            # Naresh
    escalations/ audit/ eval/ # Vinay
  components/
    ui/                       # Ruthwik — shadcn-based design system
    trace/                    # Ruthwik
    record/ timeline/         # Naga
    answer/ citations/        # Srujan
    approval/                 # Naresh
    badges/                   # Vinay
  lib/
    api/                      # Naresh — typed client + TanStack hooks
    theme.ts                  # Ruthwik
  tailwind.config.ts          # Ruthwik
```

## 8. Getting started (Ruthwik runs once, then each member builds their folder)
```bash
# from repo root
npx create-next-app@latest web --ts --tailwind --app --eslint --src-dir=false
cd web
npx shadcn@latest init                       # pick the tokens in §4.2
npx shadcn@latest add button card badge input table dialog tabs sonner
npm i lucide-react framer-motion @tanstack/react-query recharts
echo "NEXT_PUBLIC_API_BASE=http://localhost:8000" > .env.local
npm run dev                                  # http://localhost:3000
# backend in another terminal:
# cd backend && uvicorn careline.api.app:create_app --factory --reload
```

## 9. Commit conventions (same as backend)
`feat(ui-core): live console trace stepper` + `Refs: <UI-task>`. Own your paths, keep
the build green (`npm run build`), commit under your own identity. The design system
(`components/ui`, tokens) is the frozen contract — coordinate before changing it.

## 10. Milestones
1. **Foundation (Ruthwik):** scaffold + tokens + `AppShell` + `VerdictPill` + `TraceStepper` skeleton. Unblocks everyone.
2. **Vertical-slice demo:** Live Console end-to-end against `/internal/run-question` (Ruthwik + Srujan).
3. **Clinical flow (Naresh):** login → consultation → extraction → approval.
4. **Record (Naga)** + **Safety/Eval (Vinay)** screens.
5. **Polish:** empty/loading/error states, responsive, a11y pass, dark mode (optional).

---

### Per-member kickoff prompts (hand your row to your assistant)
- **Ruthwik:** "Scaffold `web/` per §8, implement the design tokens (§4.2), `AppShell` (§4.6), and the Live Agent Console calling `POST /internal/run-question`, rendering `<VerdictPill>` + `<TraceStepper>` from the response."
- **Naresh:** "Build `lib/api` typed client + Login + the Consultation flow (create → consent → extract → one-tap approve) using the endpoints in §6."
- **Naga:** "Build the Patient Record screen: `GET /patients/{id}` → current valid facts as `FactCard`s + a history `Timeline` separating current vs superseded."
- **Srujan:** "Build the grounded-answer panel: render an ANSWER with its citations and the verifier's affirmation/confidence, plus reason/verify trace detail cards."
- **Vinay:** "Build Escalations queue, Audit log, and the T1–T8 Eval dashboard with `VerdictPill` badges and Recharts."

— Owner of this doc: Ruthwik (Orchestration Lead). Design system is the shared contract.
