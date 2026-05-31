/**
 * CompanyDetail — shows submitted-vs-discovered diff, risk score, and
 * the mark-reviewed action for a single verification run.
 *
 * Renders partial/in-progress reports without crashing: sections are
 * shown based on section_statuses (pending sections show a loading notice).
 */

import { useEffect, useState } from "react";
import { apiClient, ReportResponse } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { RegistrationDiff } from "../components/RegistrationDiff";
import {
  ContactPanel,
  DomainPanel,
  RegistryPanel,
  RiskAssessmentPanel,
} from "../components/DetailPanels";

interface CompanyDetailProps {
  runId: string;
  onBack: () => void;
}

function ScoreDisplay({ score }: { score: number | null }) {
  const color =
    score === null ? "#6b7280" : score >= 70 ? "#dc2626" : score >= 40 ? "#d97706" : "#16a34a";
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
    </div>
  );
}

export function CompanyDetail({ runId, onBack }: CompanyDetailProps) {
  const { auth } = useAuth();
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mark-reviewed state
  const [reviewStatus, setReviewStatus] = useState<string | null>(null);
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
      setReviewStatus(resp.review_status);
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

          {/* Risk Score */}
          <section style={styles.section}>
            <h3 style={styles.sectionTitle}>Risk Score</h3>
            {report.section_statuses.scores === "pending" ? (
              <div style={styles.sectionPending} data-testid="score-pending">
                Score computation is pending.
              </div>
            ) : (
              <ScoreDisplay score={report.scores?.overall_score ?? null} />
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
            {reviewStatus !== null ? (
              <div
                style={styles.reviewedBanner}
                data-testid="reviewed-banner"
              >
                Marked as <strong>{reviewStatus}</strong>.
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
