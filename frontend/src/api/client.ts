/**
 * EntityIQ API client — typed wrappers for the backend REST API.
 *
 * All endpoints that require auth attach `Authorization: Bearer <token>`.
 * The `/api` prefix is proxied to `http://localhost:8000` by vite.config.ts
 * in development.  The backend paths are the real paths (no `/api` prefix on
 * the backend itself); the proxy strips it.
 */

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface SignInRequest {
  email: string;
  password: string;
}

export interface SignInResponse {
  session_token: string;
  operator_id: string;
  role: string;
  message: string;
}

export interface SubmissionRequest {
  company_name: string;
  work_email: string;
  company_domain: string;
  country: string;
  tax_id?: string;
  billing_address?: string;
  phone?: string;
  requester_full_name?: string;
  linkedin_url?: string;
}

export interface SubmissionResponse {
  submission_id: string;
  run_id: string;
  status: string;
  is_free_email_domain: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Reports list
// ---------------------------------------------------------------------------

export interface ReportListItem {
  run_id: string;
  report_id: string;
  company_name: string;
  domain: string;
  status: string; // "pending" | "partial" | "complete" | "failed"
  overall_score: number | null;
  /** pre_clear | review | escalate (critical signals can escalate a low score) */
  triage_tier?: string | null;
  review_status: string | null; // null = not reviewed yet
  generated_at: string | null;
}

export interface ReportListResponse {
  items: ReportListItem[];
  total: number;
}

// ---------------------------------------------------------------------------
// Full report (detail view)
// ---------------------------------------------------------------------------

/** One contributing signal behind a layer score (a risk flag or trust signal). */
export interface ContributingSignal {
  name: string;
  layer: string; // "entity" | "infrastructure" | "representation" | "risk"
  direction: string; // "elevated" (risk) | "trust"
  weight: number;
  description: string;
  evidence_ids?: string[];
}

export interface ScoresData {
  overall_score: number | null;
  entity_score: number | null;
  infrastructure_score: number | null;
  representation_score: number | null;
  risk_score: number | null;
  triage_tier: string | null;
  contributing_signals: ContributingSignal[];
}

export interface MismatchItem {
  id: string;
  field_name: string;
  submitted_value: string | null;
  discovered_value: string | null;
  match_status: "match" | "mismatch" | "unverified";
  evidence_id: string | null;
}

export interface EvidenceItem {
  id: string;
  source: string;
  tier: number;
  field: string | null;
  raw_value: string | null;
  normalized_value: string | null;
  confidence: number | null;
  attribution: Record<string, unknown> | null;
  fetched_at: string | null;
}

export interface SourceSummary {
  source: string;
  tier: number;
  evidence_count: number;
  attribution: Record<string, unknown> | null;
  /** "unavailable" when the source was down (timeout / error / rate limit). */
  status?: "available" | "unavailable";
}

export interface SectionStatuses {
  scores: string;
  evidence: string;
  mismatches: string;
  sources: string;
}

export interface ReviewSummary {
  status: string;
  notes: string | null;
  reviewer_name: string | null;
  decided_at: string | null;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  description: string | null;
  occurred_at: string;
  run_id: string | null;
  payload: Record<string, unknown> | null;
}

export interface AuditEventList {
  events: AuditEvent[];
}

/** Run lifecycle + timing (ticket 0001). stages: stage → pending|complete|unavailable. */
export interface RunTiming {
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  stages: Record<string, string>;
}

export interface ReportResponse {
  run_id: string;
  report_id: string;
  status: string;
  section_statuses: SectionStatuses;
  scores: ScoresData | null;
  evidence: EvidenceItem[];
  mismatches: MismatchItem[];
  sources: SourceSummary[];
  generated_at: string | null;
  /** Latest human review of this run; null until reviewed. */
  review?: ReviewSummary | null;
  /** Run status, timing and per-stage progress. */
  run?: RunTiming | null;
}

// ---------------------------------------------------------------------------
// Review
// ---------------------------------------------------------------------------

export interface MarkReviewedRequest {
  notes?: string;
  review_status?: string; // default "reviewed"
}

export interface ReviewResponse {
  review_id: string;
  run_id: string;
  operator_id: string;
  review_status: string;
  decided_at: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Operator workflow actions (P2-T10 / P2-T11)
// ---------------------------------------------------------------------------

export interface ReanalysisResponse {
  new_run_id: string;
  supersedes_run_id: string;
  entity_id: string;
  status: string;
  triggered_at: string;
  message: string;
}

/** Correctable submitted fields (must match backend _CORRECTABLE_FIELDS). */
export type CorrectableField =
  | "company_name"
  | "domain"
  | "work_email"
  | "country"
  | "tax_id"
  | "billing_address"
  | "phone"
  | "requester_full_name"
  | "linkedin_url";

export interface CorrectAndRerunRequest {
  corrections: Partial<Record<CorrectableField, string | null>>;
  notes?: string;
}

export interface CorrectAndRerunResponse {
  new_run_id: string;
  supersedes_run_id: string;
  submission_id: string;
  corrections_applied: Record<string, string | null>;
  triggered_at: string;
  message: string;
}

export interface AddNotesRequest {
  notes: string;
  review_status?: string;
}

export interface AddNotesResponse {
  review_id: string;
  run_id: string;
  notes: string;
  review_status: string;
  updated_at: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Integration API-key provisioning (ticket 0026, lead only)
// ---------------------------------------------------------------------------

export interface CreateApiClientRequest {
  name: string;
}

export interface CreateApiClientResponse {
  id: string;
  name: string;
  key_prefix: string;
  /** Plaintext — shown once. The caller must copy it now; it's never returned again. */
  api_key: string;
  active: boolean;
  created_at: string;
}

export interface ApiClientListItem {
  id: string;
  name: string;
  key_prefix: string;
  active: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiClientListResponse {
  items: ApiClientListItem[];
  total: number;
}

export interface RevokeApiClientResponse {
  id: string;
  name: string;
  active: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Domain-ownership verification (ticket 0003)
// ---------------------------------------------------------------------------

export type OwnershipMethod = "dns_txt" | "email" | "html_meta";

export interface IssueOwnershipChallengeRequest {
  method: OwnershipMethod;
  /** Required for method === "email"; ignored otherwise. */
  target?: string;
}

export interface OwnershipChallengeInstructions {
  method: OwnershipMethod;
  dns_record_type?: string | null;
  dns_record_name?: string | null;
  dns_record_value?: string | null;
  html_snippet?: string | null;
  email_target?: string | null;
}

export interface OwnershipChallenge {
  challenge_id: string;
  run_id: string;
  submission_id: string;
  domain: string;
  method: OwnershipMethod;
  /** Null for email challenges: the token only goes to the emailed address. */
  token: string | null;
  /** "pending" | "verified" */
  status: string;
  issued_at: string;
  verified_at: string | null;
  instructions: OwnershipChallengeInstructions;
  message: string;
}

export interface VerifyOwnershipChallengeRequest {
  /** Required for method === "email"; ignored otherwise. */
  submitted_token?: string;
}

export interface VerifyOwnershipChallengeResponse {
  challenge_id: string;
  method: OwnershipMethod;
  verified: boolean;
  status: string;
  verified_at: string | null;
  message: string;
}

// ---------------------------------------------------------------------------
// Individual screening (tickets 0036-0043)
// ---------------------------------------------------------------------------

export type SystemDisposition = "CLEAR" | "REVIEW" | "MATCH";

export interface ScreeningQueuePage {
  items: ScreeningQueueItem[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface ScreeningQueueItem {
  run_id: string;
  subject_name: string | null;
  status: string;
  trigger: string;
  system_disposition: SystemDisposition | null;
  auto_closed: boolean | null;
  top_score: number | null;
  human_disposition: "CLEAR" | "MATCH" | null;
  created_at: string | null;
}

export interface ScreeningClaim {
  about: "subject" | "record";
  field: string;
  value: unknown;
  source: string;
  locator: string;
  retrieved_at: string | null;
}

export interface ScreeningTermView {
  name: string;
  weight: number;
  subject_field: string;
  record_field: string;
  record_value: unknown;
  claim_ids: string[];
}

export interface ScreeningCandidateView {
  candidate_id: string;
  score: number;
  band: SystemDisposition;
  blocking_keys: string[];
  record: {
    id: string;
    source: string;
    source_entry_id: string;
    snapshot_id: string;
    primary_name: string;
    names: { name: string; kind: string }[];
    dobs: Record<string, unknown>[];
    pobs: string[];
    nationalities: string[];
    documents: { type: string; number: string; country: string | null }[];
    program: string | null;
  };
  terms: ScreeningTermView[];
  claims: Record<string, ScreeningClaim>;
}

export interface ScreeningDetail {
  run_id: string;
  subject: Record<string, unknown> | null;
  subject_shredded: boolean;
  kyb_entity_id: string | null;
  run: RunTiming & { trigger: string };
  decision: {
    decision_id: string;
    system_disposition: SystemDisposition;
    auto_closed: boolean;
    top_score: number | null;
    rule_version: number | null;
    thresholds: { clear_below: number; match_at: number };
    snapshot_ids: string[];
    normalizer_version: string;
    created_at: string | null;
  } | null;
  candidates: ScreeningCandidateView[];
  /** Present for monitoring runs (ticket 0052). */
  monitoring?: {
    snapshot_id: string;
    source: string;
    retrieved_at: string | null;
    changed_entry_ids: string[];
    prior_run_id: string | null;
    prior_disposition: SystemDisposition | null;
  } | null;
  dispositions: {
    disposition: "CLEAR" | "MATCH";
    notes: string | null;
    operator_id: string;
    created_at: string | null;
  }[];
}

export interface ReplayResult {
  reproduced: boolean | null;
  shredded: boolean;
  original: { disposition: SystemDisposition } | null;
  replayed: { disposition: SystemDisposition } | null;
  differences: { field: string; original: unknown; replayed: unknown }[];
}

// ---------------------------------------------------------------------------
// Client implementation
// ---------------------------------------------------------------------------

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const resp = await fetch(`/api${path}`, { ...options, headers });

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const err = (await resp.json()) as ApiError;
      detail = err.detail ?? detail;
    } catch {
      // ignore parse error
    }
    throw new Error(detail);
  }

  return resp.json() as Promise<T>;
}

export const apiClient = {
  /** GET /screenings: individual screening queue. */
  listScreenings(
    token: string,
    filters: { disposition?: string; trigger?: string; offset?: number } = {}
  ): Promise<ScreeningQueuePage> {
    const q = new URLSearchParams(
      Object.entries(filters)
        .filter(([, v]) => v)
        .map(([k, v]) => [k, String(v)])
    ).toString();
    return request(`/screenings${q ? `?${q}` : ""}`, {}, token);
  },

  getScreening(runId: string, token: string): Promise<ScreeningDetail> {
    return request(`/screenings/${runId}`, {}, token);
  },

  disposeScreening(
    runId: string,
    body: { disposition: "CLEAR" | "MATCH"; notes?: string },
    token: string
  ): Promise<{ disposition_id: string }> {
    return request(
      `/screenings/${runId}/disposition`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  replayScreening(runId: string, token: string): Promise<ReplayResult> {
    return request(`/screenings/${runId}/replay`, { method: "POST" }, token);
  },

  submitScreening(
    body: { name: string; dob?: string; nationality?: string },
    token: string
  ): Promise<{ run_id: string; disposition: SystemDisposition | null }> {
    return request(
      `/screenings`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** POST /auth/sign-in — returns session token */
  signIn(body: SignInRequest): Promise<SignInResponse> {
    return request<SignInResponse>("/auth/sign-in", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  /**
   * POST /auth/sign-out — invalidate the session server-side (204, no body,
   * so this bypasses the JSON-parsing request() helper). Best-effort: the
   * caller clears local auth regardless of the outcome.
   */
  async signOut(token: string): Promise<void> {
    await fetch("/api/auth/sign-out", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  },

  /** GET /audit/runs/{runId} — timeline for one run (any operator). */
  getRunAudit(runId: string, token: string): Promise<AuditEventList> {
    return request<AuditEventList>(`/audit/runs/${runId}`, {}, token);
  },

  /** GET /audit/events — global audit log (lead only; 403 otherwise). */
  getAuditLog(token: string, limit = 200): Promise<AuditEventList> {
    return request<AuditEventList>(`/audit/events?limit=${limit}`, {}, token);
  },

  /** GET /reports — dashboard list */
  listReports(token: string): Promise<ReportListResponse> {
    // No trailing slash: the backend route is exactly "/reports". A trailing
    // slash triggers a 307 redirect to the absolute backend URL, which the
    // browser can't follow cross-origin (CORS) → "Failed to fetch".
    return request<ReportListResponse>("/reports", {}, token);
  },

  /** GET /reports/{run_id} — full report for detail view */
  getReport(runId: string, token: string): Promise<ReportResponse> {
    return request<ReportResponse>(`/reports/${runId}`, {}, token);
  },

  /** POST /reviews/{run_id} — mark run as reviewed */
  markReviewed(
    runId: string,
    body: MarkReviewedRequest,
    token: string
  ): Promise<ReviewResponse> {
    return request<ReviewResponse>(
      `/reviews/${runId}`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** POST /reanalysis/{run_id} — re-trigger analysis (new run supersedes prior) */
  triggerReanalysis(runId: string, token: string): Promise<ReanalysisResponse> {
    return request<ReanalysisResponse>(
      `/reanalysis/${runId}`,
      { method: "POST" },
      token
    );
  },

  /** POST /workflow/runs/{run_id}/correct — correct submitted fields + re-run */
  correctAndRerun(
    runId: string,
    body: CorrectAndRerunRequest,
    token: string
  ): Promise<CorrectAndRerunResponse> {
    return request<CorrectAndRerunResponse>(
      `/workflow/runs/${runId}/correct`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** POST /workflow/runs/{run_id}/notes — add/append review notes */
  addNotes(
    runId: string,
    body: AddNotesRequest,
    token: string
  ): Promise<AddNotesResponse> {
    return request<AddNotesResponse>(
      `/workflow/runs/${runId}/notes`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** GET /reports/{run_id}/export — machine-readable report (for download) */
  exportReport(runId: string, token: string): Promise<ReportResponse> {
    return request<ReportResponse>(`/reports/${runId}/export`, {}, token);
  },

  /** POST /submissions — submit a new company for verification */
  submitCompany(
    body: SubmissionRequest,
    token: string,
  ): Promise<SubmissionResponse> {
    return request<SubmissionResponse>(
      "/submissions",
      { method: "POST", body: JSON.stringify(body) },
      token,
    );
  },

  /** POST /api-clients — create an integration API key (lead only; 403 for operators) */
  createApiClient(
    body: CreateApiClientRequest,
    token: string
  ): Promise<CreateApiClientResponse> {
    return request<CreateApiClientResponse>(
      "/api-clients",
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** GET /api-clients — list integration API keys (lead only) */
  listApiClients(token: string): Promise<ApiClientListResponse> {
    return request<ApiClientListResponse>("/api-clients", {}, token);
  },

  /** POST /api-clients/{id}/revoke — revoke an integration API key (lead only) */
  revokeApiClient(id: string, token: string): Promise<RevokeApiClientResponse> {
    return request<RevokeApiClientResponse>(
      `/api-clients/${id}/revoke`,
      { method: "POST" },
      token
    );
  },

  /** GET /ownership/runs/{run_id}/challenges — list challenges for a run */
  listOwnershipChallenges(
    runId: string,
    token: string
  ): Promise<OwnershipChallenge[]> {
    return request<OwnershipChallenge[]>(
      `/ownership/runs/${runId}/challenges`,
      {},
      token
    );
  },

  /** POST /ownership/runs/{run_id}/challenges — issue a domain-ownership challenge */
  issueOwnershipChallenge(
    runId: string,
    body: IssueOwnershipChallengeRequest,
    token: string
  ): Promise<OwnershipChallenge> {
    return request<OwnershipChallenge>(
      `/ownership/runs/${runId}/challenges`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },

  /** POST /ownership/challenges/{challenge_id}/verify — attempt verification */
  verifyOwnershipChallenge(
    challengeId: string,
    body: VerifyOwnershipChallengeRequest,
    token: string
  ): Promise<VerifyOwnershipChallengeResponse> {
    return request<VerifyOwnershipChallengeResponse>(
      `/ownership/challenges/${challengeId}/verify`,
      { method: "POST", body: JSON.stringify(body) },
      token
    );
  },
};
