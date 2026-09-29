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
