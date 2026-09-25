/**
 * CompanyDetail — shows submitted-vs-discovered diff, risk score, and
 * the mark-reviewed action for a single verification run.
 *
 * Renders partial/in-progress reports without crashing: sections are
 * shown based on section_statuses (pending sections show a loading notice).
 */

import { useEffect, useState } from "react";
import {
  apiClient,
  CorrectableField,
  ReportResponse,
  RunTiming,
  ReviewSummary,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { RegistrationDiff } from "../components/RegistrationDiff";
import {
  ContactPanel,
  DomainPanel,
  HqPanel,
  IdentityCorroborationPanel,
  RegistryPanel,
  RiskAssessmentPanel,
} from "../components/DetailPanels";
import { OperatorActions } from "../components/OperatorActions";
import { OwnershipPanel } from "../components/OwnershipPanel";
import { PeopleGraphPanel } from "../components/PeopleGraphPanel";
import { ActivityPanel } from "../components/ActivityPanel";

interface CompanyDetailProps {
  runId: string;
  onBack: () => void;
  /** Open another run (e.g. a re-analysis run that supersedes this one). */
  onOpenRun?: (runId: string) => void;
  /** Open an officer/owner's individual screening (ticket 0084). */
  onOpenScreening?: (screeningRunId: string) => void;
}

/** Pull prefill values for the correction form from the report's mismatches. */
function submittedValuesFromReport(
  report: ReportResponse
): Partial<Record<CorrectableField, string>> {
  const correctable = new Set<string>([
    "company_name",
    "domain",
    "work_email",
    "country",
    "tax_id",
    "billing_address",
    "phone",
    "requester_full_name",
    "linkedin_url",
  ]);
  const out: Partial<Record<CorrectableField, string>> = {};
  for (const m of report.mismatches) {
    if (correctable.has(m.field_name) && m.submitted_value) {
      out[m.field_name as CorrectableField] = m.submitted_value;
    }
  }
  return out;
}

const TIERS: Record<string, { label: string; color: string; note: string }> = {
  pre_clear: { label: "Pre-clear", color: "#16a34a", note: "low observed risk — may fast-track" },
  review: { label: "Review", color: "#d97706", note: "standard review" },
  escalate: { label: "Escalate", color: "#dc2626", note: "needs close scrutiny" },
};

function ScoreDisplay({ score, tier }: { score: number | null; tier?: string | null }) {
  const t = tier ? TIERS[tier] : undefined;
  // Tier wins over the score band: a critical signal (e.g. sanctions hit)
  // escalates regardless of score.
  const color = t
    ? t.color
    : score === null
      ? "#6b7280"
      : score >= 70
        ? "#dc2626"
        : score >= 40
          ? "#d97706"
          : "#16a34a";
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          fontSize: "3rem",
          fontWeight: 800,
          color,
          lineHeight: 1,
        }}
        data-testid="detail-overall-score"
        aria-label={`Overall risk score: ${score !== null ? score.toFixed(0) : "not available"}`}
      >
        {score !== null ? score.toFixed(0) : "—"}
      </div>
      <div style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: "0.25rem" }}>
        Overall Risk Score (0–100)
      </div>
      {t && (
        <div
          style={{ marginTop: "0.5rem", fontSize: "0.85rem", color: t.color, fontWeight: 600 }}
          data-testid="detail-triage-tier"
        >
          Triage: {t.label} — {t.note}. A human decides.
        </div>
      )}
    </div>
  );
}

/** How often an in-flight report is re-fetched so partial results land live. */
export const POLL_INTERVAL_MS = 5000;

function isInFlight(report: ReportResponse): boolean {
  if (report.run) return report.run.status === "pending" || report.run.status === "running";
  return report.status === "pending" || report.status === "partial";
}

function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${rest}s`;
  return `${rest}s`;
}

function RunProgress({ run }: { run: RunTiming }) {
  const stages = Object.values(run.stages);
  const done = stages.filter((st) => st !== "pending").length;
  const inFlight = run.status === "pending" || run.status === "running";
  return (
    <div style={styles.runProgress} data-testid="detail-run-progress">
      {inFlight
        ? `Analysis in progress: ${done} of ${stages.length} stages done. Updating automatically.`
        : run.duration_seconds != null
          ? `Analysis took ${formatDuration(run.duration_seconds)}.`
          : null}
    </div>
  );
}

export function CompanyDetail({
  runId,
  onBack,
  onOpenRun,
  onOpenScreening,
}: CompanyDetailProps) {
  const { auth } = useAuth();
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mark-reviewed state
  const [review, setReview] = useState<ReviewSummary | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient
      .getReport(runId, auth.token)
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setReview(r.review ?? null);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load report");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId, auth.token]);

  // While the run is still going, quietly re-fetch so sections fill in as
  // stages finish. A failed poll keeps the last good report on screen.
  useEffect(() => {
    if (!report || !isInFlight(report)) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      apiClient
        .getReport(runId, auth.token)
        .then((r) => {
          if (cancelled) return;
          setReport(r);
          if (r.review) setReview(r.review);
        })
        .catch(() => {
          if (!cancelled) setReport((prev) => (prev ? { ...prev } : prev));
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [report, runId, auth.token]);

  async function handleMarkReviewed() {
    if (!report) return;
    setReviewing(true);
    setReviewError(null);
    try {
      const resp = await apiClient.markReviewed(
        runId,
        {
          review_status: "reviewed",
          ...(notes.trim() ? { notes: notes.trim() } : {}),
        },
        auth.token
      );
      setReview({
        status: resp.review_status,
        notes: notes.trim() || null,
        reviewer_name: null,
        decided_at: new Date().toISOString(),
      });
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : "Failed to mark reviewed");
    } finally {
      setReviewing(false);
    }
  }

  return (
    <div>
      {/* Back navigation */}
      <button
        onClick={onBack}
        style={styles.backBtn}
        data-testid="back-button"
      >
        ← Back to Queue
      </button>

      {loading && (
        <div style={styles.state} data-testid="detail-loading">
          Loading report…
        </div>
      )}

      {error && (
        <div style={styles.errorState} role="alert" data-testid="detail-error">
          {error}
        </div>
      )}

      {!loading && !error && report && (
        <div style={styles.content}>
          {/* Header: run_id + status */}
          <div style={styles.pageHeader}>
            <div>
              <h2 style={styles.heading} data-testid="detail-run-id">
                Verification Run
              </h2>
              <code style={styles.runId}>{runId}</code>
            </div>
            <span
              style={{
                ...styles.statusBadge,
                ...(report.status === "complete"
                  ? styles.statusComplete
                  : report.status === "failed"
                    ? styles.statusFailed
                    : styles.statusPending),
              }}
              data-testid="detail-status"
            >
              {report.status}
            </span>
          </div>
          {report.run && <RunProgress run={report.run} />}

          {/* Risk Score */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Risk Score</h3>
            {report.section_statuses.scores === "pending" ? (
              <div style={styles.sectionPending} data-testid="score-pending">
                Score computation is pending.
              </div>
            ) : (
              <ScoreDisplay
                score={report.scores?.overall_score ?? null}
                tier={report.scores?.triage_tier}
              />
            )}
          </section>

          {/* Registration Diff */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Registration Data</h3>
            <p style={styles.sectionNote}>
              Submitted values compared against discovered / authoritative
              values.
            </p>
            <RegistrationDiff
              mismatches={report.mismatches}
              sectionStatus={report.section_statuses.mismatches}
            />
          </section>

          {/* DNS & Domain Intelligence */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>DNS &amp; Domain Intelligence</h3>
            <DomainPanel
              evidence={report.evidence}
              status={report.section_statuses.evidence}
              infrastructureScore={report.scores?.infrastructure_score ?? null}
            />
          </section>

          {/* Registry Information */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Registry Information</h3>
            <RegistryPanel
              evidence={report.evidence}
              status={report.section_statuses.evidence}
            />
          </section>

          {/* HQ Visualization */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Headquarters</h3>
            <HqPanel
              evidence={report.evidence}
              status={report.section_statuses.evidence}
            />
          </section>

          {/* Identity Corroboration (IC1-T6) */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Identity Corroboration</h3>
            <IdentityCorroborationPanel
              evidence={report.evidence}
              status={report.section_statuses.evidence}
              sources={report.sources}
              mismatches={report.mismatches}
            />
          </section>

          {/* Officers & owners, each screened individually (ticket 0084) */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Officers &amp; Owners</h3>
            <PeopleGraphPanel
              runId={runId}
              token={auth.token}
              companyName={
                submittedValuesFromReport(report).company_name ?? "This company"
              }
              onOpenScreening={onOpenScreening}
            />
          </section>

          {/* Contact Information */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Contact Information</h3>
            <p style={styles.sectionNote}>
              Public-web contacts shown with their source attribution.
            </p>
            <ContactPanel
              evidence={report.evidence}
              status={report.section_statuses.evidence}
            />
          </section>

          {/* Risk Assessment */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Risk Assessment</h3>
            <RiskAssessmentPanel
              scores={report.scores}
              status={report.section_statuses.scores}
              sources={report.sources}
            />
          </section>

          {/* Mark Reviewed */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Operator Action</h3>
            {review !== null ? (
              <div
                style={styles.reviewedBanner}
                data-testid="reviewed-banner"
              >
                Marked as <strong>{review.status}</strong>
                {review.reviewer_name && <> by {review.reviewer_name}</>}
                {review.decided_at && (
                  <> on {new Date(review.decided_at).toLocaleDateString()}</>
                )}
                .
                {review.notes && (
                  <p style={styles.reviewNotes} data-testid="review-notes-display">
                    {review.notes}
                  </p>
                )}
              </div>
            ) : (
              <div>
                <label htmlFor="review-notes" style={styles.notesLabel}>
                  Review notes (optional)
                </label>
                <textarea
                  id="review-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  disabled={reviewing}
                  rows={3}
                  style={styles.notesInput}
                  placeholder="Add context for your decision…"
                  data-testid="review-notes-input"
                />
                <button
                  onClick={handleMarkReviewed}
                  disabled={reviewing}
                  style={styles.reviewBtn}
                  data-testid="mark-reviewed-btn"
                >
                  {reviewing ? "Saving…" : "Mark Reviewed"}
                </button>
                {reviewError && (
                  <p
                    style={styles.reviewError}
                    role="alert"
                    data-testid="review-error"
                  >
                    {reviewError}
                  </p>
                )}
              </div>
            )}
          </section>

          {/* Domain Ownership Verification (ticket 0003) */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Domain Ownership Verification</h3>
            <OwnershipPanel runId={runId} token={auth.token} />
          </section>

          {/* Operator Actions (re-run, correct + re-run, notes, export) */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Operator Actions</h3>
            <OperatorActions
              runId={runId}
              token={auth.token}
              submittedValues={submittedValuesFromReport(report)}
              onOpenRun={onOpenRun}
              onNotesSaved={(resp) =>
                setReview((prev) => ({
                  status: resp.review_status,
                  notes: resp.notes,
                  reviewer_name: prev?.reviewer_name ?? null,
                  decided_at: prev?.decided_at ?? resp.updated_at,
                }))
              }
            />
          </section>

          {/* Activity (audit timeline) */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Activity</h3>
            <ActivityPanel runId={runId} token={auth.token} />
          </section>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backBtn: {
    background: "none",
    border: "none",
    color: "#2563eb",
    cursor: "pointer",
    fontSize: "0.875rem",
    padding: "0.5rem 0",
    marginBottom: "1rem",
    fontWeight: 500,
  },
  state: {
    padding: "2rem",
    textAlign: "center",
    color: "#6b7280",
    fontSize: "0.95rem",
  },
  errorState: {
    padding: "1rem",
    backgroundColor: "#fef2f2",
    border: "1px solid #fca5a5",
    borderRadius: "0.375rem",
    color: "#dc2626",
    fontSize: "0.875rem",
  },
  content: {
    display: "flex",
    flexDirection: "column",
    gap: "1.5rem",
  },
  pageHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: "1rem",
  },
  heading: {
    margin: "0 0 0.25rem",
    fontSize: "1.25rem",
    fontWeight: 700,
    color: "#111827",
  },
  runId: {
    fontSize: "0.75rem",
    color: "#6b7280",
    backgroundColor: "#f3f4f6",
    padding: "0.1rem 0.4rem",
    borderRadius: "0.25rem",
  },
  runProgress: {
    marginTop: "0.5rem",
    fontSize: "0.85rem",
    color: "#6b7280",
  },
  statusBadge: {
    display: "inline-block",
    padding: "0.25rem 0.75rem",
    borderRadius: "9999px",
    fontSize: "0.75rem",
    fontWeight: 600,
    textTransform: "capitalize" as React.CSSProperties["textTransform"],
  },
  statusComplete: {
    color: "#166534",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
  },
  statusFailed: {
    color: "#7f1d1d",
    backgroundColor: "#fef2f2",
    border: "1px solid #fca5a5",
  },
  statusPending: {
    color: "#92400e",
    backgroundColor: "#fffbeb",
    border: "1px solid #fcd34d",
  },
  section: {
    backgroundColor: "#ffffff",
    border: "1px solid #e5e7eb",
    borderRadius: "0.5rem",
    padding: "1.25rem",
  },
  sectionTitle: {
    margin: "0 0 0.75rem",
    fontSize: "1rem",
    fontWeight: 600,
    color: "#111827",
  },
  sectionNote: {
    margin: "0 0 0.75rem",
    fontSize: "0.8rem",
    color: "#6b7280",
  },
  sectionPending: {
    padding: "0.75rem 1rem",
    backgroundColor: "#eff6ff",
    border: "1px solid #bfdbfe",
    borderRadius: "0.375rem",
    color: "#1e40af",
    fontSize: "0.875rem",
  },
  reviewedBanner: {
    padding: "0.75rem 1rem",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
    borderRadius: "0.375rem",
    color: "#166534",
    fontSize: "0.875rem",
  },
  reviewNotes: {
    margin: "0.5rem 0 0",
    whiteSpace: "pre-wrap",
    color: "#14532d",
  },
  notesLabel: {
    display: "block",
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#374151",
    marginBottom: "0.35rem",
  },
  notesInput: {
    width: "100%",
    boxSizing: "border-box",
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    fontFamily: "inherit",
    marginBottom: "0.75rem",
    resize: "vertical",
  },
  reviewBtn: {
    padding: "0.625rem 1.25rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    border: "none",
    borderRadius: "0.375rem",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  reviewError: {
    marginTop: "0.5rem",
    color: "#dc2626",
    fontSize: "0.875rem",
  },
};
