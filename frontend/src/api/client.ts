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
}

export interface SectionStatuses {
  scores: string;
  evidence: string;
  mismatches: string;
  sources: string;
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
  /** POST /auth/sign-in — returns session token */
  signIn(body: SignInRequest): Promise<SignInResponse> {
    return request<SignInResponse>("/auth/sign-in", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  /** GET /reports/ — dashboard list */
  listReports(token: string): Promise<ReportListResponse> {
    return request<ReportListResponse>("/reports/", {}, token);
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
};
