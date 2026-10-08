"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Coins,
  Gauge,
  Info,
  Radar,
  Scale,
  ShieldCheck,
  Timer,
  Users,
} from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthError, getMonitoring, type MonitoringDrift, type MonitoringSnapshot } from "@/lib/api";
import { LatencyTrendChart, VerdictMixChart, type TrendPoint } from "./MonitoringCharts";

const POLL_MS = 5000;
const TREND_POINTS = 60; // ~5 minutes of 5 s polls

const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
const ms = (v: number) => `${v.toFixed(v < 10 ? 2 : 0)} ms`;
const usd = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `$${v < 0.01 && v > 0 ? v.toFixed(6) : v.toFixed(4)}`;
const num = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: digits });

function judgeMode(stamp: string): string {
  if (stamp.startsWith("judge (lazy")) return "Not run yet (keyless unless an API key is set)";
  if (stamp.includes("keyless")) return "Keyless (deterministic twin)";
  return "LLM judge";
}

const DRIFT_STATUS: Record<MonitoringDrift["status"], string> = {
  ok: "Reference ready — no drift",
  drift: "Drift detected",
  insufficient_data: "Collecting samples",
  reference_not_ready: "Reference building",
  reference_unavailable: "Reference unavailable",
};

export default function MonitoringPage() {
  useRequireAuth();
  const router = useRouter();
  const { data, error, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["monitoring"],
    queryFn: getMonitoring,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    retry: (count, err) => !(err instanceof AuthError) && count < 2,
  });

  useEffect(() => {
    if (error instanceof AuthError) router.replace("/login");
  }, [error, router]);

  // Keep a short client-side trend of the polled latency percentiles.
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const lastAt = useRef<number>(0);
  useEffect(() => {
    if (!data || dataUpdatedAt === lastAt.current) return;
    lastAt.current = dataUpdatedAt;
    if (data.operational.window_size === 0) return;
    const point: TrendPoint = {
      t: new Date(dataUpdatedAt).toLocaleTimeString([], { hour12: false }),
      p50: data.operational.latency_ms_p50,
      p95: data.operational.latency_ms_p95,
    };
    setTrend((prev) => [...prev, point].slice(-TREND_POINTS));
  }, [data, dataUpdatedAt]);

  const empty = data ? data.operational.window_size === 0 : false;

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <Gauge className="h-5 w-5 text-primary" aria-hidden />
              <span className="text-xs font-semibold uppercase tracking-wide text-primary">
                LLMOps · online monitoring
              </span>
            </div>
            <h1 className="text-2xl font-semibold text-ink">Monitoring dashboard</h1>
            <p className="mt-1 text-sm text-muted">
              Cost and latency per request, verdict mix, online judge quality and input drift —
              live from <code className="text-xs">GET /monitoring</code>, refreshed every 5 s.
            </p>
          </div>
          <p className="text-xs text-muted" aria-live="polite">
            {data ? `Snapshot ${data.generated_at}` : isLoading ? "Loading…" : ""}
          </p>
        </div>

        <Card>
          <CardBody className="flex items-start gap-3 py-4">
            <Info className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
            <p className="text-sm leading-6 text-muted">
              <span className="font-semibold text-ink">Scope: {data?.scope ?? "process-wide, aggregate, no PHI"}.</span>{" "}
              Every signed-in doctor sees the whole deployment&apos;s aggregate — not a per-doctor
              slice. No question text, patient identifiers or fact text is stored or shown. Counters
              reset when the API process restarts; the window holds the last{" "}
              {data ? num(data.operational.window_capacity) : "1,000"} turns.
            </p>
          </CardBody>
        </Card>

        {error && !(error instanceof AuthError) ? (
          <Card>
            <CardBody className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-escalate" aria-hidden />
              <p className="text-sm text-muted" role="alert">
                Monitoring unavailable ({error instanceof Error ? error.message : String(error)}).
                Retrying every 5 s.
              </p>
            </CardBody>
          </Card>
        ) : null}

        {data && data.alerts.length > 0 ? (
          <div
            role="alert"
            className="rounded-2xl border border-escalate/30 bg-escalate-bg px-6 py-4 text-sm text-escalate"
          >
            <p className="font-semibold">Active alerts</p>
            <ul className="mt-1 list-disc pl-5">
              {data.alerts.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {data && empty ? (
          <Card>
            <CardBody className="text-sm leading-6 text-muted">
              No turns recorded yet in this API process. Ask a question in the{" "}
              <Link href="/console" className="text-primary hover:underline">
                Live Console
              </Link>{" "}
              or the patient portal and the tiles below fill in on the next refresh.
            </CardBody>
          </Card>
        ) : null}

        {data ? <Sections data={data} trend={trend} empty={empty} /> : null}
      </div>
    </AppShell>
  );
}

function Sections({
  data,
  trend,
  empty,
}: {
  data: MonitoringSnapshot;
  trend: TrendPoint[];
  empty: boolean;
}) {
  const { operational: op, output, quality, drift, cost, human_feedback: hf } = data;
  const decided = output.verdict_counts.answer + output.verdict_counts.clarify + output.verdict_counts.escalate;

  return (
    <>
      {/* (a) Operational */}
      <Section
        icon={Timer}
        title="Operational"
        subtitle={`Latency per request over the last ${num(op.window_size)} turns · ${num(op.requests_total)} total since start`}
      >
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Tile label="Latency p50" value={empty ? "—" : ms(op.latency_ms_p50)} />
          <Tile label="Latency p95" value={empty ? "—" : ms(op.latency_ms_p95)} />
          <Tile label="Latency p99" value={empty ? "—" : ms(op.latency_ms_p99)} />
          <Tile label="Error rate" value={empty ? "—" : pct(op.error_rate)} hint={`${op.errors} errors`} tone={op.error_rate > 0.01 ? "bad" : undefined} />
          <Tile
            label="Fail-closed rate"
            value={empty ? "—" : pct(op.fail_closed_rate)}
            hint={`${op.fail_closed} escalated on dependency failure`}
            tone={op.fail_closed_rate > 0.05 ? "bad" : undefined}
          />
          <Tile
            label="Throughput"
            value={`${num(op.throughput_rpm_1m)} /min`}
            hint={op.throughput_rps_window === null ? "last 60 s" : `${op.throughput_rps_window} req/s over window`}
          />
        </div>
        <div className="mt-5">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
            Latency trend (this browser session)
          </p>
          {trend.length > 1 ? (
            <LatencyTrendChart points={trend} />
          ) : (
            <Empty>Trend appears after two refreshes with recorded turns.</Empty>
          )}
        </div>
      </Section>

      {/* (b) Output */}
      <Section icon={ShieldCheck} title="Output" subtitle="Verdict mix of the windowed turns — escalation is the safe default">
        <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
          <div className="grid content-start gap-4">
            <Tile label="Escalation rate" value={decided ? pct(output.escalation_rate) : "—"} hint={`${output.verdict_counts.escalate} of ${decided} turns`} />
            <Tile
              label="Low-risk escalation (proxy)"
              value={decided ? pct(output.low_risk_escalation_rate) : "—"}
              hint={output.low_risk_escalation_note}
            />
          </div>
          <div>
            {decided ? <VerdictMixChart counts={output.verdict_counts} /> : <Empty>No verdicts yet.</Empty>}
            {decided ? (
              <p className="mt-2 text-xs text-muted">
                Answer {pct(output.verdict_rates.answer)} · Clarify {pct(output.verdict_rates.clarify)} · Escalate{" "}
                {pct(output.verdict_rates.escalate)}
              </p>
            ) : null}
          </div>
        </div>
      </Section>

      {/* (c) Quality / online eval */}
      <Section
        icon={Scale}
        title="Quality · online evaluation"
        subtitle={`LLM-as-judge on a ${pct(quality.sample_rate, 0)} sample of ANSWER turns, scored only against the facts valid at that turn`}
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="Judge mode" value={judgeMode(quality.judge)} hint={quality.judge} small />
          <Tile label="Judged samples" value={num(quality.judged)} hint={`${quality.sampled} sampled · ${quality.pending} pending`} />
          <Tile
            label="Faithfulness rate"
            value={pct(quality.faithfulness_rate)}
            hint={quality.mean_score === null ? "no judged answers yet" : `mean score ${quality.mean_score}`}
            tone={quality.judged >= 10 && (quality.faithfulness_rate ?? 1) < 0.9 ? "bad" : undefined}
          />
          <Tile label="Judge errors / dropped" value={`${quality.judge_errors} / ${quality.dropped}`} hint="errors are never scored faithful" />
        </div>
      </Section>

      {/* (c2) Human feedback — the second online-eval channel */}
      <Section
        icon={Users}
        title="Human feedback (online eval)"
        subtitle="Patient thumbs on answers / clarifications and doctor review of answers — aggregates only, no comment text"
      >
        {hf && (hf.patient_ratings > 0 || hf.doctor_reviews > 0) ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Patient ratings" value={num(hf.patient_ratings)} hint={`${hf.patient_helpful} marked helpful`} />
            <Tile
              label="Patient helpful rate"
              value={pct(hf.patient_helpful_rate)}
              hint="alert < 70% with ≥ 20 ratings"
              tone={hf.patient_ratings >= 20 && (hf.patient_helpful_rate ?? 1) < 0.7 ? "bad" : undefined}
            />
            <Tile label="Doctor reviews" value={num(hf.doctor_reviews)} hint={`${hf.doctor_reviews_answer} of ANSWER turns`} />
            <Tile
              label="Doctor-rated accuracy"
              value={pct(hf.doctor_rated_accuracy)}
              hint={`${hf.doctor_correct_answer} / ${hf.doctor_reviews_answer} answers correct · alert < 90% with ≥ 10`}
              tone={hf.doctor_reviews_answer >= 10 && (hf.doctor_rated_accuracy ?? 1) < 0.9 ? "bad" : undefined}
            />
          </div>
        ) : (
          <Empty>
            {hf
              ? "No human feedback yet — patients rate answers in the portal; doctors review them in the Audit log."
              : "This backend does not report human feedback yet."}
          </Empty>
        )}
      </Section>

      {/* (d) Input & drift */}
      <Section icon={Radar} title="Input & drift" subtitle="Live question distribution vs the eval-set reference (aggregate features only)">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile
            label="Reference status"
            value={DRIFT_STATUS[drift.status]}
            hint={
              drift.status === "insufficient_data"
                ? `${drift.samples ?? 0} / ${drift.min_samples ?? 30} turns needed`
                : drift.error ?? (drift.drifted ? drift.reasons.join("; ") : undefined)
            }
            tone={drift.drifted ? "bad" : undefined}
            small
          />
          <Tile
            label="Scope-mix PSI"
            value={drift.scope_psi === undefined ? "—" : drift.scope_psi.toFixed(3)}
            hint={drift.psi_threshold === undefined ? "needs reference + 30 turns" : `flag > ${drift.psi_threshold}`}
          />
          <Tile
            label="OOV token rate"
            value={pct(drift.oov_rate)}
            hint={drift.reference_oov_rate === undefined ? "vs eval vocabulary" : `baseline ${pct(drift.reference_oov_rate)}`}
          />
          <Tile
            label="Mean length shift"
            value={
              drift.mean_tokens === undefined || !drift.reference_mean_tokens
                ? "—"
                : pct((drift.mean_tokens - drift.reference_mean_tokens) / drift.reference_mean_tokens)
            }
            hint={
              drift.mean_tokens === undefined
                ? "flag > 50%"
                : `${drift.mean_tokens} vs ${drift.reference_mean_tokens} tokens`
            }
          />
        </div>
        <p className="mt-3 text-xs text-muted">
          Drift flag: <span className="font-semibold text-ink">{drift.drifted ? "RAISED" : "clear"}</span>
        </p>
      </Section>

      {/* (e) Cost */}
      <Section icon={Coins} title="Cost" subtitle={`${cost.basis} — ${cost.includes}`}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Tile label="Mean $ / request" value={usd(cost.mean_cost_usd_per_request)} hint="keyless turns cost $0" />
          <Tile label="p95 $ / request" value={usd(cost.p95_cost_usd_per_request)} />
          <Tile label="Mean tokens / request" value={num(cost.mean_tokens_per_request, 1)} />
          <Tile label="Total tokens" value={num(cost.total_tokens)} hint={`${cost.requests_with_llm_calls} requests made LLM calls`} />
          <Tile label="Total cost (window)" value={usd(cost.total_cost_usd)} hint={`${cost.requests_unknown_cost} requests with unknown cost`} />
        </div>
      </Section>
    </>
  );
}

function Section({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: typeof Gauge;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title}>
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <Icon className="h-5 w-5 text-primary" aria-hidden />
              {title}
            </span>
          }
          subtitle={subtitle}
        />
        <CardBody>{children}</CardBody>
      </Card>
    </section>
  );
}

function Tile({
  label,
  value,
  hint,
  tone,
  small,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "bad";
  small?: boolean;
}) {
  return (
    <div className={`rounded-xl border px-4 py-3 ${tone === "bad" ? "border-escalate/40 bg-escalate-bg" : "border-border bg-canvas"}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 font-semibold ${tone === "bad" ? "text-escalate" : "text-ink"} ${small ? "text-base" : "text-2xl"}`}>
        {value}
      </p>
      {hint ? <p className="mt-1 break-words text-xs leading-5 text-muted">{hint}</p> : null}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-24 items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted">
      {children}
    </div>
  );
}
