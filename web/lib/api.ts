// Typed client for the CareLine demo backend (Live Agent Console).
// Production screens use the authenticated API via Naresh's lib/api client; this
// foundation talks to the zero-setup demo endpoints (careline.demo_server).

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Verdict = "answer" | "clarify" | "escalate";
export type StepStatus = "pass" | "terminal" | "skipped";

export interface TraceStep {
  name: string;
  status: StepStatus;
  spec_section: string | null;
  detail: string | null;
}

export interface AnswerResult {
  verdict: Verdict;
  answer_text: string | null;
  escalation_reason: string | null;
  confidence: number;
  risk: number;
  citations: string[];
  trace: TraceStep[];
}

export interface DemoFact {
  id: string;
  kind: string;
  summary: string;
}

export interface DemoPatient {
  patient_id: string;
  doctor_id: string;
  backend: string;
  current_facts: DemoFact[];
}

export async function ask(question: string): Promise<AnswerResult> {
  const res = await fetch(`${BASE}/demo/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`Demo API error ${res.status}`);
  return res.json();
}

export async function getDemoPatient(): Promise<DemoPatient> {
  const res = await fetch(`${BASE}/demo/patient`);
  if (!res.ok) throw new Error(`Demo API error ${res.status}`);
  return res.json();
}
