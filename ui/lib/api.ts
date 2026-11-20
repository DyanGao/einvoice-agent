// "localhost", like the UI: with sign-in on, the session cookie (SameSite=Strict)
// only travels between the UI and an API on the same site.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Sent with every request; the API refuses changes made with the session cookie without it. */
const UI_HEADER = { "X-Requested-By": "einvoice-ui" };

/** Fired when the API says the session is gone, so the page can ask to sign in again. */
export const SIGNED_OUT_EVENT = "einvoice:signed-out";

export type ReviewState = "pending" | "approved" | "not_required" | "rejected";
export type RejectReason = "duplicate" | "not_addressed_to_us" | "replaced_or_cancelled" | "other";

/** Where a document was booked, or is waiting to be: one per system. */
export type Booking = {
  connector: string;
  status: "pending" | "delivered" | "failed";
  external_ref: string | null;
};

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
  bookings: Booking[];
};

export type Fields = Record<string, unknown>;

export type Violation = {
  rule_id: string;
  severity: string;
  message: string;
  location: string | null;
};

/** A text the server wrote, as a code with parameters (app/texts.py) and in
 * English. Null for rows from before the server sent codes. */
export type Said = { code: string; params: Record<string, unknown>; text: string };

/** Something a business-rule check found on the invoice. */
export type Finding = {
  type: string;
  severity: "review" | "info";
  message: string;
  text?: Said | null;
  fields: string[];
  detail: Record<string, unknown>;
};

/** One reason the invoice cannot be booked without a person. */
export type Reason = {
  cause: string;
  message: string;
  text?: Said | null;
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
  error_text?: Said | null;
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
  /** The same lines as codes; null where a line has none. */
  explanation_texts?: (Said | null)[] | null;
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

/** What the API refused, in English; `said` is its code when the API sent one,
 * for the UI's language. Status 0: the API could not be reached at all. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly said: Said | null = null,
  ) {
    super(message);
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      ...init,
      credentials: "include",
      headers: { ...UI_HEADER, ...init?.headers },
    });
  } catch {
    throw new ApiError(0, `The API at ${API_URL} is not reachable. Start it with "make api".`);
  }
  if (!response.ok) {
    const { message, said } = await refusal(response);
    throw new ApiError(response.status, message, said);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return (await (await send(path, init)).json()) as T;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    }
    throw error;
  }
}

/** The name for the history, sent only with sign-in off; signed in, the API knows who it is. */
function reviewerHeader(reviewer: string): Record<string, string> {
  return reviewer ? { "X-Reviewer": reviewer } : {};
}

async function refusal(response: Response): Promise<{ message: string; said: Said | null }> {
  const fallback = `The API answered ${response.status} ${response.statusText}`.trim();
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown; text?: Said };
    const said = body.text ?? null;
    if (typeof body.detail === "string") return { message: body.detail, said };
    if (body.detail !== undefined) return { message: JSON.stringify(body.detail), said };
  } catch {
    // Not JSON (for example a proxy error page): the status line is all we have.
  }
  return { message: fallback, said: null };
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
    headers: { "Content-Type": "application/json", ...reviewerHeader(reviewer) },
    body: JSON.stringify({ changes }),
  });
}

export function recheckDocument(id: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/recheck`, { method: "POST" });
}

export function approveDocument(id: string, reviewer: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    headers: reviewerHeader(reviewer),
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
    headers: { "Content-Type": "application/json", ...reviewerHeader(reviewer) },
    body: JSON.stringify({ reason, note: note.trim() || null }),
  });
}

/** A plain copy of an earlier invoice (app/db/duplicates.py). */
export type DuplicateCopy = Summary & { original: Summary };

export function listDuplicateCopies(): Promise<{ copies: DuplicateCopy[] }> {
  return request("/review/duplicates");
}

export function rejectDuplicateCopies(
  ids: string[],
  reviewer: string,
): Promise<{ rejected: string[]; skipped: string[] }> {
  return request("/review/duplicates/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...reviewerHeader(reviewer) },
    body: JSON.stringify({ ids }),
  });
}

export function reopenDocument(id: string, reviewer: string, note: string): Promise<Detail> {
  return request(`/documents/${encodeURIComponent(id)}/reopen`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...reviewerHeader(reviewer) },
    body: JSON.stringify({ note }),
  });
}

export type Role = "viewer" | "reviewer" | "admin";

/** What the sign-in page offers. */
export type SignInOptions =
  | { auth: "off" }
  | { auth: "on"; provider: boolean; provider_name: string | null };

export type Tenant = { id: string; slug: string; name: string };

export type Me = {
  auth: "on";
  name: string;
  email: string | null;
  role: Role;
  /** The tenant (company) this session works in. */
  tenant: Tenant;
  /** Every tenant the person is in, with their role there. */
  tenants: (Tenant & { role: Role })[];
};

export function getSignInOptions(): Promise<SignInOptions> {
  return send("/auth/options").then((response) => response.json());
}

/** Who is signed in; null when nobody is. Not `request`: a 401 here is an answer. */
export async function getMe(): Promise<Me | { auth: "off" } | null> {
  try {
    return await (await send("/auth/me")).json();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function signIn(email: string, password: string): Promise<Me> {
  const response = await send("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return response.json();
}

export async function signOut(): Promise<void> {
  await send("/auth/logout", { method: "POST" });
}

/** A browser navigation, not a fetch: the identity provider's page takes over. */
export const PROVIDER_SIGN_IN_URL = `${API_URL}/auth/oidc/start`;

/** Work in another of one's tenants in this browser session. */
export function switchTenant(tenantId: string): Promise<Me> {
  return request("/auth/tenant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: tenantId }),
  });
}

export type Member = {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  disabled: boolean;
  /** The built-in password account; everyone else signs in through the provider. */
  password: boolean;
};

export function listMembers(): Promise<{ members: Member[] }> {
  return request("/admin/members");
}

export function addMember(email: string, name: string, role: Role): Promise<Member> {
  return request("/admin/members", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name, role }),
  });
}

export function changeMemberRole(userId: string, role: Role): Promise<Member> {
  return request(`/admin/members/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export async function removeMember(userId: string): Promise<void> {
  await send(`/admin/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
}

export type TenantSettings = {
  tenant: Tenant;
  settings: Record<string, unknown>;
  /** What the tenant's admin may change here; the rest is the operator's. */
  editable: string[];
};

export function getTenantSettings(): Promise<TenantSettings> {
  return request("/admin/settings");
}

export function changeTenantSettings(changes: Record<string, unknown>): Promise<TenantSettings> {
  return request("/admin/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes }),
  });
}

/** What became of one file attached to a mail. */
export type MailPart = {
  filename: string | null;
  content_type: string;
  bytes: number;
  outcome: "ingested" | "known" | "refused";
  reason: string | null;
  document_id: string | null;
};

export type Mail = {
  id: string;
  read_at: string;
  sender: string;
  subject: string;
  sent: string;
  parts: MailPart[];
};

export function listMail(): Promise<{ mails: Mail[] }> {
  return request("/mail?limit=100");
}
