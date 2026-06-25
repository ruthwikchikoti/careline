// Typed client for the CareLine demo backend (Live Agent Console).
// Production screens use the authenticated API via Naresh's lib/api client; this
// foundation talks to the zero-setup demo endpoints (careline.demo_server).

import { getToken } from "@/lib/auth";

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

export async function ask(question: string, patientId?: string): Promise<AnswerResult> {
  // Send the doctor's token when signed in so the demo turn is attributed to
  // them — that's what makes console escalations appear in their queue. The
  // endpoint stays auth-free, so an anonymous demo (no token) still works.
  // When patientId is set (and signed in), the answer is grounded in that real
  // registered patient's persisted facts instead of the bundled demo patient.
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}/demo/ask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, patient_id: patientId || null }),
  });
  if (!res.ok) throw new Error(`Demo API error ${res.status}`);
  return res.json();
}

export async function getDemoPatient(): Promise<DemoPatient> {
  const res = await fetch(`${BASE}/demo/patient`);
  if (!res.ok) throw new Error(`Demo API error ${res.status}`);
  return res.json();
}

// --- Authenticated clinical API (Naresh / ui-clinical) ---

export class AuthError extends Error {
  constructor(message = "Authentication required") {
    super(message);
    this.name = "AuthError";
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface PatientRegisterIn {
  patient_id: string;
  caller_id: string;
  pin: string;
}

export interface PatientOut {
  patient_id: string;
  doctor_id: string;
  fact_count: number;
}

export type ConsultationStatus = "draft" | "approved" | "archived";

export interface ConsultationCreateIn {
  patient_id: string;
  transcript?: string | null;
}

export interface ConsultationOut {
  consultation_id: string;
  doctor_id: string;
  patient_id: string;
  status: ConsultationStatus;
  created_at: string;
  fact_count: number;
}

export interface ExtractOut {
  consultation_id: string;
  fact_count: number;
  status: ConsultationStatus;
}

export interface ApprovalOut {
  consultation_id: string;
  status: ConsultationStatus;
  applied_facts: number;
  retired_facts: number;
}

export interface ErasureOut {
  patient_id: string;
  layer1_nulled: number;
  layer2_dropped: boolean;
  audit_redacted: number;
}

export interface FactRecord {
  id: string;
  kind: string;
  summary: string;
  effective_from: string;
  superseded_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  current: boolean;
}

export interface PatientRecord {
  patient_id: string;
  doctor_id: string;
  as_of: string;
  current: FactRecord[];
  history: FactRecord[];
}

async function authFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  if (!token) throw new AuthError();

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let detail = `API error ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function login(doctorId: string): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doctor_id: doctorId }),
  });
  if (!res.ok) {
    let detail = `Login failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export function registerPatient(body: PatientRegisterIn): Promise<PatientOut> {
  return authFetch<PatientOut>("/patients", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listPatients(): Promise<PatientOut[]> {
  return authFetch<PatientOut[]>("/patients");
}

export function getPatientRecord(id: string): Promise<PatientRecord> {
  return authFetch<PatientRecord>(`/patients/${encodeURIComponent(id)}/record`);
}

export function createConsultation(body: ConsultationCreateIn): Promise<ConsultationOut> {
  return authFetch<ConsultationOut>("/consultations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listConsultations(): Promise<ConsultationOut[]> {
  return authFetch<ConsultationOut[]>("/consultations");
}

export function getConsultation(id: string): Promise<ConsultationOut> {
  return authFetch<ConsultationOut>(`/consultations/${encodeURIComponent(id)}`);
}

export function stampConsent(id: string, purpose: string): Promise<ConsultationOut> {
  return authFetch<ConsultationOut>(`/consultations/${encodeURIComponent(id)}/consent`, {
    method: "POST",
    body: JSON.stringify({ purpose }),
  });
}

export function extractFacts(id: string): Promise<ExtractOut> {
  return authFetch<ExtractOut>(`/consultations/${encodeURIComponent(id)}/extract`, {
    method: "POST",
  });
}

export function approveFacts(id: string): Promise<ApprovalOut> {
  return authFetch<ApprovalOut>(`/consultations/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });
}

export function erasePatientData(patientId: string): Promise<ErasureOut> {
  return authFetch<ErasureOut>(`/patients/${encodeURIComponent(patientId)}/data`, {
    method: "DELETE",
  });
}

// --- Observability: audit / escalations / eval (Vinay / eval) ---

export interface AuditTurn {
  turn_id: string;
  call_id: string;
  patient_id: string;
  logged_at: string;
  verdict: Verdict;
  question: string | null;
  answer_text: string | null;
  escalation_reason: string | null;
  confidence: number;
  risk: number;
  trace_steps: TraceStep[];
  redacted: boolean;
  resolved: boolean;
  reply: string | null;
  resolved_at: string | null;
}

export interface AuditCall {
  call_id: string;
  patient_id: string;
  started_at: string;
  ended_at: string | null;
  turn_count: number;
  final_verdict: Verdict | null;
  escalated: boolean;
  redacted: boolean;
}

export interface AuditLog {
  calls: AuditCall[];
  turns: AuditTurn[];
}

export interface EscalationGroup {
  patient_id: string;
  count: number;
  latest_at: string;
  escalations: AuditTurn[];
}

export interface EscalationsQueue {
  waiting: number;
  patients_waiting: number;
  groups: EscalationGroup[];
  escalations: AuditTurn[];
}

export interface EvalScenarioResult {
  name: string;
  verdict: Verdict;
  passed: boolean;
}

export interface EvalRun {
  passed: number;
  total: number;
  digest: string;
  scenarios: EvalScenarioResult[];
}

export function getAuditLog(): Promise<AuditLog> {
  return authFetch<AuditLog>("/audit");
}

export function getEscalations(): Promise<EscalationsQueue> {
  return authFetch<EscalationsQueue>("/escalations");
}

export interface EscalationResolved {
  turn_id: string;
  patient_id: string;
  reply: string;
  resolved_at: string;
}

export function resolveEscalation(turnId: string, reply: string): Promise<EscalationResolved> {
  return authFetch<EscalationResolved>(`/escalations/${encodeURIComponent(turnId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ reply }),
  });
}

export function runEval(): Promise<EvalRun> {
  return authFetch<EvalRun>("/eval");
}
