/**
 * Dashboard — operator queue showing analyzed companies.
 *
 * Displays: company name, analysis date, risk score, review status.
 * Loading and empty states are handled.  Clicking a row navigates to
 * the company detail view (hash-based routing via `onSelect`).
 *
 * A submitted company is verified in the background; the queue shows a notice
 * and re-polls until its report lands, then says whether it's ready or failed.
 * If the queue can't be refreshed, polling stops and the notice says so.
 */

import { useEffect, useState } from "react";
import { apiClient, ReportListItem } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { NewCompanyForm } from "../components/NewCompanyForm";

interface DashboardProps {
  onSelect: (runId: string) => void;
  /** How often to re-poll the queue while a submission is running (ms). */
  pollMs?: number;
}

interface BackgroundRun {
  runId: string;
  companyName: string;
  // running → checking (report landed; reading its run status) →
  // ready | failed. error: the queue couldn't be refreshed; polling stopped.
  state: "running" | "checking" | "ready" | "failed" | "error";
}

const TIER_COLOR: Record<string, string> = {
  escalate: "#dc2626",
  review: "#d97706",
  pre_clear: "#16a34a",
};
const TIER_LABEL: Record<string, string> = {
  escalate: "Escalate",
  review: "Review",
  pre_clear: "Pre-clear",
};

/** Tier wins over the score band: a sanctions hit escalates even at score 22. */
function scoreColor(score: number | null, tier?: string | null): string {
  if (tier && TIER_COLOR[tier]) return TIER_COLOR[tier];
  if (score === null) return "#6b7280";
  if (score >= 70) return "#dc2626"; // high risk — red
  if (score >= 40) return "#d97706"; // medium risk — amber
  return "#16a34a"; // low risk — green
}

function scoreLabel(score: number | null): string {
  if (score === null) return "—";
  return score.toFixed(0);
}

function reviewBadge(status: string | null) {
  if (status === null) {
    return (
      <span style={badgeStyles.pending} data-testid="review-status-badge">
        Pending Review
      </span>
    );
  }
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <span style={badgeStyles.reviewed} data-testid="review-status-badge">
      {label}
    </span>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

type ReviewFilter = "all" | "pending" | "reviewed";
type TierFilter = "all" | "escalate" | "review" | "pre_clear";

export function Dashboard({ onSelect, pollMs = 5000 }: DashboardProps) {
  const { auth } = useAuth();
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters / search (client-side over the fetched list)
  const [search, setSearch] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [tierFilter, setTierFilter] = useState<TierFilter>("all");

  // New-company form modal + queue refresh trigger
  const [showForm, setShowForm] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [backgroundRuns, setBackgroundRuns] = useState<BackgroundRun[]>([]);

  // Refreshes (after a submit, or while polling) update the list in place
  // rather than flashing the loading state; only the first load shows it.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    apiClient
      .listReports(auth.token)
      .then((resp) => {
        if (!cancelled) {
          setItems(resp.items);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
          setLoading(false);
          setBackgroundRuns((runs) =>
            runs.some((r) => r.state === "running")
              ? runs.map((r) => (r.state === "running" ? { ...r, state: "error" } : r))
              : runs
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [auth.token, refreshKey]);

  // A run's report appears in the list only once its pipeline finishes (a
  // failed run keeps a partial one), so read the run's status once it lands.
  useEffect(() => {
    const landed = new Set(items.map((i) => i.run_id));
    setBackgroundRuns((runs) =>
      runs.some((r) => r.state === "running" && landed.has(r.runId))
        ? runs.map((r) =>
            r.state === "running" && landed.has(r.runId) ? { ...r, state: "checking" } : r
          )
        : runs
    );
  }, [items]);

  const checkingKey = backgroundRuns
    .filter((r) => r.state === "checking")
    .map((r) => r.runId)
    .join(",");
  useEffect(() => {
    if (!checkingKey) return;
    for (const runId of checkingKey.split(",")) {
      apiClient
        .getReport(runId, auth.token)
        .then(
          (report) => (report.run?.status === "failed" ? "failed" : "ready"),
          // The report is in the queue either way; don't block on this read.
          () => "ready" as const
        )
        .then((state) =>
          setBackgroundRuns((runs) =>
            runs.map((r) => (r.runId === runId && r.state === "checking" ? { ...r, state } : r))
          )
        );
    }
  }, [checkingKey, auth.token]);

  const stillRunning = backgroundRuns.some((r) => r.state === "running");
  useEffect(() => {
    if (!stillRunning) return;
    const timer = setInterval(() => setRefreshKey((k) => k + 1), pollMs);
    return () => clearInterval(timer);
  }, [stillRunning, pollMs]);

  const query = search.trim().toLowerCase();
  const filtered = items.filter((item) => {
    if (query) {
      const haystack = `${item.company_name} ${item.domain}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    if (reviewFilter === "pending" && item.review_status !== null) return false;
    if (reviewFilter === "reviewed" && item.review_status === null) return false;
    if (tierFilter !== "all" && item.triage_tier !== tierFilter) {
      return false;
    }
    return true;
  });

  const filtersActive =
    query !== "" || reviewFilter !== "all" || tierFilter !== "all";

  return (
    <div>
      <div style={styles.header}>
        <h2 style={styles.heading}>Verification Queue</h2>
        <span style={styles.count}>
          {loading
            ? ""
            : filtersActive
              ? `${filtered.length} of ${items.length} report${items.length !== 1 ? "s" : ""}`
              : `${items.length} report${items.length !== 1 ? "s" : ""}`}
        </span>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          style={styles.newBtn}
          data-testid="open-new-company-form"
        >
          + Check New Company
        </button>
      </div>

      {showForm && (
        <NewCompanyForm
          token={auth.token}
          onClose={() => setShowForm(false)}
          onSuccess={(resp, companyName) => {
            setShowForm(false);
            setBackgroundRuns((runs) => [
              ...runs,
              { runId: resp.run_id, companyName, state: "running" },
            ]);
            setRefreshKey((k) => k + 1);
          }}
        />
      )}

      {backgroundRuns.map((r) => (
        <div
          key={r.runId}
          style={
            r.state === "ready"
              ? styles.noticeReady
              : r.state === "failed" || r.state === "error"
                ? styles.noticeFailed
                : styles.notice
          }
          role="status"
          data-testid="background-run-notice"
        >
          <span>
            {r.state === "ready" && (
              <>
                <strong>{r.companyName}</strong> is ready — its report is now
                in the queue.
              </>
            )}
            {r.state === "failed" && (
              <>
                Data collection for <strong>{r.companyName}</strong> failed.
                Its partial report is in the queue.
              </>
            )}
            {r.state === "error" && (
              <>
                Couldn't check on <strong>{r.companyName}</strong> — the queue
                didn't refresh. Reload the page to see whether it has finished.
              </>
            )}
            {(r.state === "running" || r.state === "checking") && (
              <>
                Data collection for <strong>{r.companyName}</strong> is
                running in the background. It will appear in the queue when
                it finishes; you can keep working.
              </>
            )}
          </span>
          <button
            type="button"
            onClick={() =>
              setBackgroundRuns((runs) => runs.filter((x) => x.runId !== r.runId))
            }
            style={styles.noticeDismiss}
            aria-label="Dismiss"
            data-testid="dismiss-background-notice"
          >
            ×
          </button>
        </div>
      ))}

      {!loading && !error && items.length > 0 && (
        <div style={styles.filterBar} data-testid="dashboard-filters">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search company or domain…"
            aria-label="Search company or domain"
            style={styles.searchInput}
            data-testid="dashboard-search"
          />
          <select
            value={reviewFilter}
            onChange={(e) => setReviewFilter(e.target.value as ReviewFilter)}
            aria-label="Filter by review status"
            style={styles.select}
            data-testid="filter-review"
          >
            <option value="all">All statuses</option>
            <option value="pending">Pending review</option>
            <option value="reviewed">Reviewed</option>
          </select>
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value as TierFilter)}
            aria-label="Filter by triage tier"
            style={styles.select}
            data-testid="filter-tier"
          >
            <option value="all">All tiers</option>
            <option value="escalate">{TIER_LABEL.escalate}</option>
            <option value="review">{TIER_LABEL.review}</option>
            <option value="pre_clear">{TIER_LABEL.pre_clear}</option>
          </select>
        </div>
      )}

      {loading && (
        <div style={styles.state} data-testid="dashboard-loading">
          Loading reports…
        </div>
      )}

      {error && (
        <div style={styles.errorState} role="alert" data-testid="dashboard-error">
          {error}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div style={styles.state} data-testid="dashboard-empty">
          No reports yet. Submissions will appear here once analysis is
          complete.
        </div>
      )}

      {!loading && !error && items.length > 0 && filtered.length === 0 && (
        <div style={styles.state} data-testid="dashboard-no-matches">
          No reports match the current filters.
        </div>
      )}

      {!loading && !error && filtered.length > 0 && (
        <table style={styles.table} data-testid="dashboard-table">
          <thead>
            <tr>
              <th style={styles.th}>Company</th>
              <th style={styles.th}>Domain</th>
              <th style={styles.th}>Analysis Date</th>
              <th style={{ ...styles.th, textAlign: "center" }}>Risk Score</th>
              <th style={styles.th}>Triage</th>
              <th style={styles.th}>Review Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => (
              <tr
                key={item.run_id}
                style={styles.row}
                onClick={() => onSelect(item.run_id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onSelect(item.run_id);
                }}
                aria-label={`View details for ${item.company_name}`}
                data-testid={`dashboard-row-${item.run_id}`}
              >
                <td style={styles.td}>
                  <strong>{item.company_name || "—"}</strong>
                </td>
                <td style={{ ...styles.td, color: "#6b7280" }}>{item.domain}</td>
                <td style={styles.td}>{formatDate(item.generated_at)}</td>
                <td style={{ ...styles.td, textAlign: "center" }}>
                  <span
                    style={{
                      fontWeight: 700,
                      fontSize: "1rem",
                      color: scoreColor(item.overall_score, item.triage_tier),
                    }}
                    data-testid="risk-score"
                    aria-label={`Risk score: ${scoreLabel(item.overall_score)}`}
                  >
                    {scoreLabel(item.overall_score)}
                  </span>
                </td>
                <td style={styles.td}>
                  {item.triage_tier && TIER_LABEL[item.triage_tier] ? (
                    <span
                      style={{
                        ...badgeStyles.tier,
                        color: TIER_COLOR[item.triage_tier],
                        borderColor: TIER_COLOR[item.triage_tier],
                      }}
                      data-testid="triage-badge"
                    >
                      {TIER_LABEL[item.triage_tier]}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td style={styles.td}>{reviewBadge(item.review_status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const badgeStyles: Record<string, React.CSSProperties> = {
  tier: {
    display: "inline-block",
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    fontSize: "0.75rem",
    fontWeight: 600,
    border: "1px solid",
    backgroundColor: "#ffffff",
  },
  pending: {
    display: "inline-block",
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    fontSize: "0.75rem",
    fontWeight: 600,
    color: "#92400e",
    backgroundColor: "#fffbeb",
    border: "1px solid #fcd34d",
  },
  reviewed: {
    display: "inline-block",
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    fontSize: "0.75rem",
    fontWeight: 600,
    color: "#1d4ed8",
    backgroundColor: "#eff6ff",
    border: "1px solid #bfdbfe",
  },
};

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: "flex",
    alignItems: "baseline",
    gap: "1rem",
    marginBottom: "1rem",
  },
  heading: {
    margin: 0,
    fontSize: "1.25rem",
    fontWeight: 700,
    color: "#111827",
  },
  count: {
    color: "#6b7280",
    fontSize: "0.875rem",
  },
  newBtn: {
    marginLeft: "auto",
    padding: "0.5rem 1rem",
    border: "none",
    borderRadius: "0.375rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    fontWeight: 600,
    fontSize: "0.875rem",
    cursor: "pointer",
  },
  notice: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    marginBottom: "0.75rem",
    padding: "0.75rem 1rem",
    backgroundColor: "#eff6ff",
    border: "1px solid #93c5fd",
    borderRadius: "0.375rem",
    color: "#1e3a8a",
    fontSize: "0.85rem",
  },
  noticeReady: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    marginBottom: "0.75rem",
    padding: "0.75rem 1rem",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
    borderRadius: "0.375rem",
    color: "#166534",
    fontSize: "0.85rem",
  },
  noticeFailed: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    marginBottom: "0.75rem",
    padding: "0.75rem 1rem",
    backgroundColor: "#fef2f2",
    border: "1px solid #fca5a5",
    borderRadius: "0.375rem",
    color: "#991b1b",
    fontSize: "0.85rem",
  },
  noticeDismiss: {
    marginLeft: "auto",
    background: "none",
    border: "none",
    color: "inherit",
    fontSize: "1.1rem",
    lineHeight: 1,
    cursor: "pointer",
  },
  filterBar: {
    display: "flex",
    flexWrap: "wrap",
    gap: "0.5rem",
    marginBottom: "1rem",
  },
  searchInput: {
    flex: "1 1 220px",
    minWidth: "180px",
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
  },
  select: {
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    backgroundColor: "#ffffff",
    cursor: "pointer",
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
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "0.875rem",
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
    borderRadius: "0.5rem",
    overflow: "hidden",
  },
  th: {
    padding: "0.75rem 1rem",
    backgroundColor: "#f9fafb",
    borderBottom: "2px solid #e5e7eb",
    color: "#374151",
    fontWeight: 600,
    textAlign: "left",
  },
  row: {
    cursor: "pointer",
    borderBottom: "1px solid #e5e7eb",
    transition: "background-color 0.1s",
  },
  td: {
    padding: "0.75rem 1rem",
    color: "#111827",
    verticalAlign: "middle",
  },
};
