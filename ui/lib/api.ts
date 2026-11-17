export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type ReviewState = "pending" | "approved" | "not_required" | "rejected";
export type RejectReason = "duplicate" | "not_addressed_to_us" | "replaced_or_cancelled" | "other";

export type Summary = {
  id: string;
  filename: string | null;
  format: string;
  status: string;
  review_state: ReviewState;
  created_at: string;
  findings_to_review: number;
  number: string | null;
  seller_name: string | null;
  gross_total: string | null;
  currency: string | null;
};

export type Fields = Record<string, unknown>;

export type Violation = {
  rule_id: string;
  severity: string;
  message: string;
  location: string | null;
};

/** Something a business-rule check found on the invoice. */
export type Finding = {
  type: string;
  severity: "review" | "info";
  message: string;
  fields: string[];
  detail: Record<string, unknown>;
};

/** One reason the invoice cannot be booked without a person. */
export type Reason = {
  cause: string;
  message: string;
  fields: string[];
  findings: string[];
};

/** Whether the invoice may be booked without a person, and why not. */
export type Decision = { route: "auto" | "review"; reasons: Reason[]; version: string };

/** The invoice on its way to a downstream system. */
export type Delivery = {
  connector: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  external_ref: string | null;
  last_error: string | null;
  delivered_at: string | null;
};

/** What the invoice was matched to in the ERP. */
export type Match = {
  supplier_id: string | null;
  po_number: string | null;
  po_basis: string;
  delivery_id: string | null;
  payments: { bank_transaction_id: string; amount: string }[];
  payment_basis: string;
  explanation: string[];
};

export type AuditEvent = {
  seq: number;
  event: string;
  actor: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
};

export type Detail = Summary & {
  error: string | null;
  content_type: string | null;
  fields: Fields | null;
  original: Fields | null;
  missing: string[];
  validation: { valid: boolean; scenario: string | null; violations: Violation[] } | null;
  match: Match | null;
  decision: Decision | null;
  deliveries: Delivery[];
  findings: Finding[];
  audit: AuditEvent[];
};

export type UploadResult = { id: string; status: string; duplicate: boolean };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, init);
  } catch {
    throw new ApiError(0, `The API at ${API_URL} is not reachable. Start it with "make api".`);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorText(response));
  }
  return (await response.json()) as T;
}

async function errorText(response: Response): Promise<string> {
  const fallback = `The API answered ${response.status} ${response.statusText}`.trim();
  const text = await response.text();
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  } catch {
    // Not JSON (for example a proxy error page): the status line is all we have.
  }
  return fallback;
}

export function contentUrl(id: string): string {
  return `${API_URL}/documents/${encodeURIComponent(id)}/content`;
}

export function listDocuments(reviewState?: ReviewState): Promise<{ documents: Summary[] }> {
  const query = reviewState ? `?review_state=${reviewState}&limit=200` : "?limit=200";
  return request(`/documents${query}`);
}

export function getDocument(id: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}`);
}

export function uploadDocument(file: File): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  return request("/documents", { method: "POST", body });
}

export function correctFields(
  id: string,
  changes: Record<string, unknown>,
  reviewer: string,
): Promise<Detail & { changed: string[] }> {
  return request(`/documents/${encodeURIComponent(id)}/fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Reviewer": reviewer },
    body: JSON.stringify({ changes }),
  });
}

export function recheckDocument(id: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/recheck`, { method: "POST" });
}

export function approveDocument(id: string, reviewer: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    headers: { "X-Reviewer": reviewer },
  });
}

export function rejectDocument(
  id: string,
  reviewer: string,
  reason: RejectReason,
  note: string,
): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Reviewer": reviewer },
    body: JSON.stringify({ reason, note: note.trim() || null }),
  });
}

/** A plain copy of an earlier invoice (app/db/duplicates.py). */
export type CopySummary = {
  id: string;
  number: string | null;
  seller_name: string | null;
  gross_total: string | null;
  currency: string | null;
  created_at: string;
  review_state: ReviewState;
};
export type DuplicateCopy = CopySummary & { original: CopySummary };

export function listDuplicateCopies(): Promise<{ copies: DuplicateCopy[] }> {
  return request("/review/duplicates");
}

export function rejectDuplicateCopies(
  ids: string[],
  reviewer: string,
): Promise<{ rejected: string[]; skipped: string[] }> {
  return request("/review/duplicates/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Reviewer": reviewer },
    body: JSON.stringify({ ids }),
  });
}
