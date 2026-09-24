/**
 * Individuals queue (ticket 0043): screening runs, most severe first.
 * Operators can also screen a person from here (intake, ticket 0036).
 */

import { useEffect, useState } from "react";
import { apiClient, ScreeningQueueItem } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { DispositionBadge } from "../../components/DispositionBadge";

export function IndividualsQueue({ onSelect }: { onSelect: (runId: string) => void }) {
  const { auth } = useAuth();
  const [items, setItems] = useState<ScreeningQueueItem[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [trigger, setTrigger] = useState("");
  const [name, setName] = useState("");
  const [dob, setDob] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .listScreenings(auth.token, {
        disposition: filter || undefined,
        trigger: trigger || undefined,
      })
      .then((r) => {
        if (cancelled) return;
        setItems(r.items);
        setTotal(r.total ?? r.items.length);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [auth.token, filter, trigger, refresh]);

  async function loadMore() {
    if (!items) return;
    setLoadingMore(true);
    try {
      const r = await apiClient.listScreenings(auth.token, {
        disposition: filter || undefined,
        trigger: trigger || undefined,
        offset: items.length,
      });
      setItems([...items, ...r.items]);
      setTotal(r.total ?? total);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingMore(false);
    }
  }

  async function screen(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    try {
      const r = await apiClient.submitScreening(
        { name: name.trim(), dob: dob.trim() || undefined },
        auth.token
      );
      setName("");
      setDob("");
      onSelect(r.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
      setRefresh((n) => n + 1);
    }
  }

  return (
    <div>
      <div style={styles.headerRow}>
        <h2 style={styles.heading}>Individuals</h2>
        <select
          data-testid="filter-disposition"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={styles.select}
          aria-label="Filter by disposition"
        >
          <option value="">All dispositions</option>
          <option value="MATCH">MATCH</option>
          <option value="REVIEW">REVIEW</option>
          <option value="CLEAR">CLEAR</option>
        </select>
        <select
          data-testid="filter-trigger"
          value={trigger}
          onChange={(e) => setTrigger(e.target.value)}
          style={styles.select}
          aria-label="Filter by trigger"
        >
          <option value="">All triggers</option>
          <option value="intake">Intake</option>
          <option value="monitoring">Monitoring alerts</option>
        </select>
      </div>

      {auth.role !== "examiner" && (
        <form onSubmit={screen} style={styles.form} data-testid="screen-person-form">
          <input
            placeholder="Full name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={styles.input}
            aria-label="Full name"
          />
          <input
            placeholder="DOB (YYYY-MM-DD)"
            value={dob}
            onChange={(e) => setDob(e.target.value)}
            style={styles.input}
            aria-label="Date of birth"
          />
          <button type="submit" disabled={submitting} style={styles.button}>
            {submitting ? "Screening…" : "Screen person"}
          </button>
        </form>
      )}

      {error && <div role="alert" style={styles.error}>{error}</div>}
      {items === null && !error && <div>Loading…</div>}
      {items !== null && (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Subject</th>
              <th style={styles.th}>System</th>
              <th style={styles.th}>Human</th>
              <th style={styles.th}>Top score</th>
              <th style={styles.th}>Trigger</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr
                key={i.run_id}
                data-testid="screening-row"
                onClick={() => onSelect(i.run_id)}
                style={styles.row}
              >
                <td style={styles.td}>{i.subject_name ?? "(shredded)"}</td>
                <td style={styles.td}>
                  <DispositionBadge value={i.system_disposition} auto={i.auto_closed} />
                </td>
                <td style={styles.td}>{i.human_disposition ?? "—"}</td>
                <td style={styles.td}>{i.top_score?.toFixed(2) ?? "—"}</td>
                <td style={styles.td}>
                  {i.trigger === "monitoring" ? (
                    <span data-testid="monitoring-label" style={styles.monitoring}>
                      Monitoring alert
                    </span>
                  ) : (
                    i.trigger
                  )}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={5} style={styles.td}>No screenings.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {items !== null && total !== null && items.length > 0 && (
        <div style={styles.footer}>
          <span data-testid="queue-count">
            Showing {items.length} of {total}
          </span>
          {items.length < total && (
            <button onClick={loadMore} disabled={loadingMore} style={styles.button}>
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  heading: { margin: "0 0 1rem", fontSize: "1.25rem", color: "#111827" },
  select: { padding: "0.375rem 0.5rem", borderRadius: "0.375rem", border: "1px solid #d1d5db" },
  form: { display: "flex", gap: "0.5rem", marginBottom: "1rem" },
  input: { padding: "0.375rem 0.5rem", borderRadius: "0.375rem", border: "1px solid #d1d5db" },
  button: { padding: "0.375rem 0.75rem", borderRadius: "0.375rem", border: "none",
            backgroundColor: "#1e3a5f", color: "#fff", cursor: "pointer" },
  error: { color: "#991b1b", marginBottom: "0.75rem" },
  table: { width: "100%", borderCollapse: "collapse", backgroundColor: "#fff" },
  th: { textAlign: "left", padding: "0.5rem", borderBottom: "1px solid #e5e7eb",
        fontSize: "0.75rem", color: "#6b7280", textTransform: "uppercase" },
  td: { padding: "0.5rem", borderBottom: "1px solid #f3f4f6", fontSize: "0.875rem" },
  row: { cursor: "pointer" },
  footer: { display: "flex", justifyContent: "space-between", alignItems: "center",
            marginTop: "0.75rem", fontSize: "0.875rem", color: "#6b7280" },
  monitoring: { backgroundColor: "#ede9fe", color: "#5b21b6", padding: "0.125rem 0.5rem",
                borderRadius: "9999px", fontSize: "0.75rem", fontWeight: 600 },
};
