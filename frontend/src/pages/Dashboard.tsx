/**
 * Dashboard — operator queue showing analyzed companies.
 *
 * Displays: company name, analysis date, risk score, review status.
 * Loading and empty states are handled.  Clicking a row navigates to
 * the company detail view (hash-based routing via `onSelect`).
 */

import { useEffect, useState } from "react";
import { apiClient, ReportListItem } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { NewCompanyForm } from "../components/NewCompanyForm";

interface DashboardProps {
  onSelect: (runId: string) => void;
}

function scoreColor(score: number | null): string {
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
type RiskFilter = "all" | "low" | "medium" | "high";

/** Risk band for a score: low < 40 ≤ medium < 70 ≤ high. */
function riskBand(score: number | null): RiskFilter | null {
  if (score === null) return null;
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}

export function Dashboard({ onSelect }: DashboardProps) {
  const { auth } = useAuth();
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters / search (client-side over the fetched list)
  const [search, setSearch] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");

  // New-company form modal + queue refresh trigger
  const [showForm, setShowForm] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
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
        }
      });
    return () => {
      cancelled = true;
    };
  }, [auth.token, refreshKey]);

  const query = search.trim().toLowerCase();
  const filtered = items.filter((item) => {
    if (query) {
      const haystack = `${item.company_name} ${item.domain}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    if (reviewFilter === "pending" && item.review_status !== null) return false;
    if (reviewFilter === "reviewed" && item.review_status === null) return false;
    if (riskFilter !== "all" && riskBand(item.overall_score) !== riskFilter) {
      return false;
    }
    return true;
  });

  const filtersActive =
    query !== "" || reviewFilter !== "all" || riskFilter !== "all";

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
          onSuccess={() => {
            setShowForm(false);
            setRefreshKey((k) => k + 1);
          }}
        />
      )}

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
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value as RiskFilter)}
            aria-label="Filter by risk level"
            style={styles.select}
            data-testid="filter-risk"
          >
            <option value="all">All risk levels</option>
            <option value="high">High (70+)</option>
            <option value="medium">Medium (40–69)</option>
            <option value="low">Low (&lt;40)</option>
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
                      color: scoreColor(item.overall_score),
                    }}
                    data-testid="risk-score"
                    aria-label={`Risk score: ${scoreLabel(item.overall_score)}`}
                  >
                    {scoreLabel(item.overall_score)}
                  </span>
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
