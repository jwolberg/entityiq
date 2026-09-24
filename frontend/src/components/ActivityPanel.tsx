/**
 * ActivityPanel — the audit timeline for one verification run (P3-T2).
 * Fetches its own data so the report page renders even if audit reads fail.
 */

import { useEffect, useState } from "react";
import { apiClient, AuditEvent } from "../api/client";
import { eventLabel } from "./eventLabel";

export function ActivityPanel({ runId, token }: { runId: string; token: string }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .getRunAudit(runId, token)
      .then((r) => !cancelled && setEvents(r.events))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [runId, token]);

  if (error) {
    return (
      <div style={styles.muted} data-testid="activity-error">
        Activity history is unavailable right now.
      </div>
    );
  }
  if (events === null) return <div style={styles.muted}>Loading activity…</div>;
  if (events.length === 0) {
    return <div style={styles.muted}>No recorded activity yet.</div>;
  }

  return (
    <ol style={styles.list}>
      {events.map((e) => (
        <li key={e.id} style={styles.item} data-testid="activity-event">
          <div style={styles.head}>
            <span style={styles.action}>{eventLabel(e.event_type)}</span>
            <span style={styles.when}>{new Date(e.occurred_at).toLocaleString()}</span>
          </div>
          <div style={styles.actor}>{e.actor}</div>
          {e.description && <div style={styles.desc}>{e.description}</div>}
        </li>
      ))}
    </ol>
  );
}

const styles: Record<string, React.CSSProperties> = {
  muted: { fontSize: "0.85rem", color: "#6b7280" },
  list: { listStyle: "none", margin: 0, padding: 0, borderLeft: "2px solid #e5e7eb" },
  item: { padding: "0.4rem 0 0.6rem 0.9rem", position: "relative" },
  head: { display: "flex", justifyContent: "space-between", gap: "1rem" },
  action: { fontWeight: 600, fontSize: "0.85rem", textTransform: "capitalize" },
  when: { fontSize: "0.75rem", color: "#6b7280", whiteSpace: "nowrap" },
  actor: { fontSize: "0.8rem", color: "#374151" },
  desc: { fontSize: "0.8rem", color: "#6b7280", marginTop: "0.15rem" },
};
