/**
 * RegistrationDiff — displays submitted-vs-discovered field comparisons
 * with clear match/mismatch/unverified indicators.
 *
 * Renders partial/in-progress reports without crashing: when mismatches
 * is empty and section_statuses.mismatches is "pending", a pending notice
 * is shown instead of an empty table.
 */

import { MismatchItem, SectionStatuses } from "../api/client";

interface RegistrationDiffProps {
  mismatches: MismatchItem[];
  sectionStatus: SectionStatuses["mismatches"];
}

const FIELD_LABELS: Record<string, string> = {
  company_name: "Company Name",
  domain: "Domain",
  country: "Country",
  tax_id: "Tax ID",
  tax_id_registered_name: "Registered Name (tax ID)",
  linkedin_website: "Website (LinkedIn)",
  billing_address: "Billing Address",
  phone: "Phone",
};

function fieldLabel(fieldName: string): string {
  return FIELD_LABELS[fieldName] ?? fieldName.replace(/_/g, " ");
}

const STATUS_CONFIG = {
  match: {
    label: "Match",
    color: "#16a34a",
    bg: "#f0fdf4",
    border: "#86efac",
    symbol: "✓",
  },
  mismatch: {
    label: "Mismatch",
    color: "#dc2626",
    bg: "#fef2f2",
    border: "#fca5a5",
    symbol: "✗",
  },
  unverified: {
    label: "Unverified",
    color: "#92400e",
    bg: "#fffbeb",
    border: "#fcd34d",
    symbol: "?",
  },
} as const;

function StatusBadge({
  status,
}: {
  status: "match" | "mismatch" | "unverified";
}) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unverified;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.25rem",
        padding: "0.125rem 0.5rem",
        borderRadius: "9999px",
        fontSize: "0.75rem",
        fontWeight: 600,
        color: cfg.color,
        backgroundColor: cfg.bg,
        border: `1px solid ${cfg.border}`,
      }}
      aria-label={cfg.label}
    >
      <span aria-hidden="true">{cfg.symbol}</span>
      {cfg.label}
    </span>
  );
}

export function RegistrationDiff({
  mismatches,
  sectionStatus,
}: RegistrationDiffProps) {
  if (sectionStatus === "pending") {
    return (
      <div style={styles.pending} data-testid="diff-pending">
        Field comparison is being computed — check back shortly.
      </div>
    );
  }

  if (mismatches.length === 0) {
    return (
      <div style={styles.empty} data-testid="diff-empty">
        No field comparisons available for this report.
      </div>
    );
  }

  return (
    <div data-testid="registration-diff">
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Field</th>
            <th style={styles.th}>Submitted</th>
            <th style={styles.th}>Discovered</th>
            <th style={{ ...styles.th, textAlign: "center" }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {mismatches.map((item) => {
            const cfg =
              STATUS_CONFIG[item.match_status as keyof typeof STATUS_CONFIG] ??
              STATUS_CONFIG.unverified;
            return (
              <tr
                key={item.id}
                style={{
                  backgroundColor: cfg.bg,
                  borderBottom: "1px solid #e5e7eb",
                }}
                data-testid={`diff-row-${item.field_name}`}
                data-status={item.match_status}
              >
                <td style={styles.td}>
                  <strong>{fieldLabel(item.field_name)}</strong>
                </td>
                <td style={styles.td}>{item.submitted_value ?? "—"}</td>
                <td style={styles.td}>{item.discovered_value ?? "—"}</td>
                <td style={{ ...styles.td, textAlign: "center" }}>
                  <StatusBadge
                    status={
                      item.match_status as "match" | "mismatch" | "unverified"
                    }
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  pending: {
    padding: "0.75rem 1rem",
    backgroundColor: "#eff6ff",
    border: "1px solid #bfdbfe",
    borderRadius: "0.375rem",
    color: "#1e40af",
    fontSize: "0.875rem",
  },
  empty: {
    padding: "0.75rem 1rem",
    backgroundColor: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
    color: "#6b7280",
    fontSize: "0.875rem",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "0.875rem",
  },
  th: {
    textAlign: "left",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#f9fafb",
    borderBottom: "2px solid #e5e7eb",
    color: "#374151",
    fontWeight: 600,
  },
  td: {
    padding: "0.5rem 0.75rem",
    verticalAlign: "middle",
    color: "#111827",
  },
};
