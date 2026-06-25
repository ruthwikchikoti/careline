const TOKEN_KEY = "careline_access_token";

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

// --- patient portal session (separate from the doctor session) ---

const PATIENT_TOKEN_KEY = "careline_patient_token";

export function setPatientToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(PATIENT_TOKEN_KEY, token);
}

export function getPatientToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(PATIENT_TOKEN_KEY);
}

export function clearPatientToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(PATIENT_TOKEN_KEY);
}

export function isPatientAuthenticated(): boolean {
  return getPatientToken() !== null;
}
