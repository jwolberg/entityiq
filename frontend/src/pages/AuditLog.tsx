/**
 * AuditLog — global audit log for compliance leads (P3-T2, USERS § 3).
 * The API enforces lead-only access; the nav link is hidden for operators.
 */

import { useEffect, useState } from "react";
import { apiClient, AuditEvent } from "../api/client";
import { eventLabel } from "../components/eventLabel";

interface AuditLogProps {
  token: string;
  onOpenRun: (runId: string) => void;
}

export function AuditLog({ token, onOpenRun }: AuditLogProps) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    apiClient
      .getAuditLog(token)
      .then((r) => setEvents(r.events))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, [token]);

  if (error) return <p role="alert">Could not load the audit log: {error}</p>;
  if (events === null) return <p>Loading audit log…</p>;

  const q = filter.trim().toLowerCase();
  const shown = q
    ? events.filter((e) =>
        [e.event_type, e.actor, e.description ?? ""].some((v) =>
          v.toLowerCase().includes(q)
        )
      )
    : events;

  return (
    <div>
      <h2 style={styles.title}>Audit Log</h2>
      <p style={styles.note}>
        Every operator and integration action, newest first. Append-only.
      </p>
      <input
        placeholder="Filter by action, actor, or description"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        style={styles.filter}
      />
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>When</th>
            <th style={styles.th}>Action</th>
            <th style={styles.th}>Actor</th>
            <th style={styles.th}>Details</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((e) => (
            <tr key={e.id} data-testid="audit-row">
              <td style={styles.td}>{new Date(e.occurred_at).toLocaleString()}</td>
              <td style={{ ...styles.td, textTransform: "capitalize" }}>
                {eventLabel(e.event_type)}
              </td>
              <td style={styles.td}>{e.actor}</td>
              <td style={styles.td}>
                {e.description}
                {e.run_id && (
                  <button style={styles.link} onClick={() => onOpenRun(e.run_id!)}>
                    Open run
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  title: { margin: "0 0 0.25rem" },
  note: { margin: "0 0 1rem", color: "#6b7280", fontSize: "0.875rem" },
  filter: {
    width: "100%",
    maxWidth: "360px",
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    marginBottom: "1rem",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" },
  th: {
    textAlign: "left",
    padding: "0.5rem",
    borderBottom: "2px solid #e5e7eb",
    color: "#374151",
  },
  td: { padding: "0.5rem", borderBottom: "1px solid #f3f4f6", verticalAlign: "top" },
  link: {
    marginLeft: "0.5rem",
    background: "none",
    border: "none",
    color: "#2563eb",
    cursor: "pointer",
    padding: 0,
    fontSize: "0.8rem",
  },
};
