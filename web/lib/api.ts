import type { AuditEvent, Claim, Note, SessionSummary, SoapNote } from "./types";

// Requests go through the rewrite in next.config.ts, so there is no CORS setup.
const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function readDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d as { msg?: string }).msg)
      .filter(Boolean)
      .join("; ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let message = res.statusText || "Request failed";
    try {
      message = readDetail(await res.json(), message);
    } catch {
      // The body was not JSON. Keep the status text.
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export const listSessions = () => request<SessionSummary[]>("/sessions");

export const getSession = (id: number) => request<SessionSummary>(`/sessions/${id}`);

export async function getNote(sessionId: number): Promise<Note | null> {
  try {
    return await request<Note>(`/sessions/${sessionId}/note`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export const createDraft = (sessionId: number, rawInput: string) =>
  request<Note>(`/sessions/${sessionId}/draft`, {
    method: "POST",
    body: JSON.stringify({ raw_input: rawInput }),
  });

export const saveNote = (sessionId: number, finalNote: SoapNote) =>
  request<Note>(`/sessions/${sessionId}/note`, {
    method: "PUT",
    body: JSON.stringify({ final_note: finalNote }),
  });

// The idempotency key makes a repeated click or a network retry return the first
// result instead of an error.
export const signNote = (sessionId: number, idempotencyKey: string) =>
  request<Note>(`/sessions/${sessionId}/note/sign`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
  });

export async function getClaim(sessionId: number): Promise<Claim | null> {
  try {
    return await request<Claim>(`/sessions/${sessionId}/claim`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export const getAudit = (sessionId: number) =>
  request<AuditEvent[]>(`/sessions/${sessionId}/note/audit`);
