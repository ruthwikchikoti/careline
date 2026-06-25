import Link from "next/link";
import {
  ArrowRight,
  Clock,
  FileCheck2,
  PhoneCall,
  ShieldAlert,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";

const SPINE = ["triage", "retrieve", "reason", "verify", "gate"];
const ROUTES = [
  { label: "answer", tone: "text-answer bg-answer-bg" },
  { label: "clarify", tone: "text-clarify bg-clarify-bg" },
  { label: "escalate", tone: "text-escalate bg-escalate-bg" },
];

const FEATURES = [
  {
    icon: Clock,
    title: "Temporal truth",
    body: "Every fact carries a half-open validity window. A discontinued medicine or an expired diet is structurally absent from the answer path — never quoted as current.",
  },
  {
    icon: ShieldAlert,
    title: "Fail-closed escalation",
    body: "Uncertainty, missing context, or any unavailable dependency always resolves toward ESCALATE — the call goes to the doctor, never to a guess.",
  },
  {
    icon: UserRoundCheck,
    title: "One-patient isolation",
    body: "Every query is tenant- and patient-scoped at the storage layer. A cross-patient read is an absent code path, not a forgotten check.",
  },
  {
    icon: FileCheck2,
    title: "Doctor-approved grounding",
    body: "The agent answers only from facts a doctor has explicitly approved and that are valid right now. It surfaces the record — it never originates advice.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-canvas">
      {/* top bar */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold text-ink">CareLine</span>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/console" className="text-sm font-medium text-muted hover:text-ink">
            Live Console
          </Link>
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90"
          >
            Doctor login <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </header>

      {/* hero */}
      <section className="mx-auto max-w-4xl px-6 pb-10 pt-12 text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted">
          <PhoneCall className="h-3.5 w-3.5 text-primary" />
          Post-consultation AI voice agent
        </span>
        <h1 className="mt-5 text-balance text-4xl font-semibold leading-tight tracking-tight text-ink sm:text-5xl">
          Follow-up answers your patients can trust —{" "}
          <span className="text-primary">and escalations you can rely on.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-pretty text-base leading-7 text-muted">
          After a consultation, patients call back with questions. CareLine answers only from
          that one patient&apos;s doctor-approved, currently-valid record — and hands the call
          straight to you the instant anything is serious, out-of-scope, stale, or uncertain.
          It is a 7-agent system with a deterministic safety spine, not a chatbot.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/console"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90"
          >
            Try the Live Console <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-medium text-ink transition-colors hover:border-primary hover:text-primary"
          >
            Sign in as a doctor
          </Link>
        </div>
        <p className="mt-3 text-xs text-muted">
          Demo login: <span className="font-medium text-ink">dr-asha</span> · 5 patients already on file
        </p>
      </section>

      {/* the safety spine */}
      <section className="mx-auto max-w-4xl px-6 py-6">
        <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
          <p className="mb-4 text-center text-xs font-semibold uppercase tracking-wide text-muted">
            The 7-agent safety spine — every call follows the same route
          </p>
          <div className="flex flex-wrap items-center justify-center gap-2 text-sm">
            {SPINE.map((node, i) => (
              <div key={node} className="flex items-center gap-2">
                <span className="rounded-lg bg-primary-muted px-3 py-1.5 font-medium text-primary">
                  {node}
                </span>
                {i < SPINE.length - 1 && <ArrowRight className="h-4 w-4 text-muted" />}
              </div>
            ))}
            <ArrowRight className="h-4 w-4 text-muted" />
            <div className="flex flex-col gap-1">
              {ROUTES.map((r) => (
                <span
                  key={r.label}
                  className={`rounded-lg px-3 py-1 text-center text-xs font-medium ${r.tone}`}
                >
                  {r.label}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* features */}
      <section className="mx-auto max-w-5xl px-6 py-10">
        <div className="grid gap-4 sm:grid-cols-2">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
            >
              <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary-muted text-primary">
                <Icon className="h-5 w-5" />
              </span>
              <h3 className="text-base font-semibold text-ink">{title}</h3>
              <p className="mt-1.5 text-sm leading-6 text-muted">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* the rule */}
      <section className="mx-auto max-w-3xl px-6 pb-16">
        <div className="rounded-2xl border border-primary/20 bg-primary-muted/40 p-6 text-center">
          <ShieldCheck className="mx-auto mb-3 h-6 w-6 text-primary" />
          <p className="text-pretty text-lg font-medium leading-8 text-ink">
            “Uncertainty always resolves toward escalate. Never answer from a superseded fact.
            One patient per call — zero cross-patient reachability.”
          </p>
          <p className="mt-3 text-xs uppercase tracking-wide text-muted">
            The overriding safety rule every part of the system serves
          </p>
        </div>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/console"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary/90"
          >
            Open the Live Console <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/eval"
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-medium text-ink transition-colors hover:border-primary hover:text-primary"
          >
            See the T1–T8 safety eval
          </Link>
        </div>
      </section>

      <footer className="border-t border-border py-6 text-center text-xs text-muted">
        CareLine · Multi-agent orchestration capstone · answers ground in approved facts, uncertainty escalates
      </footer>
    </div>
  );
}
