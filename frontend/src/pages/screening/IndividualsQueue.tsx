/**
 * Individuals queue (ticket 0043): screening runs, most severe first.
 * Operators can also screen a person from here (intake, ticket 0036). The
 * screening runs in the background: the operator stays on the queue, sees a
 * notice, and gets the outcome with a link when it finishes (ticket 0075).
 */

import { useEffect, useRef, useState } from "react";
import { apiClient, ScreeningQueueItem, SystemDisposition } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { DispositionBadge } from "../../components/DispositionBadge";
import { SCREENING_POLL_MS } from "./IndividualDetail";

interface BackgroundScreening {
  runId: string;
  name: string;
  // error: its status couldn't be read; polling stopped.
  state: "running" | "done" | "failed" | "error";
  disposition: SystemDisposition | null;
}

export function IndividualsQueue({
  onSelect,
  pollMs = SCREENING_POLL_MS,
}: {
  onSelect: (runId: string) => void;
  /** How often to check a background screening's status (ms). */
  pollMs?: number;
}) {
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
  const [background, setBackground] = useState<BackgroundScreening[]>([]);
  const [pollTick, setPollTick] = useState(0);
  // A background refresh reloads everything already loaded (not just page
  // one), so "Load more" pages survive it. Consumed by the next list load.
  const itemsRef = useRef<ScreeningQueueItem[] | null>(null);
  itemsRef.current = items;
  const refreshLimit = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    const limit = refreshLimit.current;
    refreshLimit.current = undefined;
    apiClient
      .listScreenings(auth.token, {
        disposition: filter || undefined,
        trigger: trigger || undefined,
        limit,
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

  // Poll only the runs we started (each read is an audited view, like the
  // detail page's own poll); refresh the queue once when one finishes.
  const running = background.filter((b) => b.state === "running").map((b) => b.runId);
  const runningKey = running.join(",");
  useEffect(() => {
    if (!runningKey) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const results = await Promise.all(
        runningKey.split(",").map((runId) =>
          apiClient.getScreening(runId, auth.token).then(
            (d) => ({ runId, d, failed: false }),
            () => ({ runId, d: null, failed: true })
          )
        )
      );
      if (cancelled) return;
      const finished = new Map<string, BackgroundScreening["state"]>();
      const dispositions = new Map<string, SystemDisposition | null>();
      for (const { runId, d, failed } of results) {
        const status = d?.run.status;
        if (failed) {
          finished.set(runId, "error");
        } else if (status && status !== "pending" && status !== "running") {
          finished.set(runId, status === "failed" ? "failed" : "done");
          dispositions.set(runId, d?.decision?.system_disposition ?? null);
        }
      }
      if (finished.size === 0) {
        setPollTick((n) => n + 1);
        return;
      }
      setBackground((bs) =>
        bs.map((b) =>
          finished.has(b.runId)
            ? { ...b, state: finished.get(b.runId)!, disposition: dispositions.get(b.runId) ?? null }
            : b
        )
      );
      const loaded = itemsRef.current ?? [];
      const done = [...finished].filter(([, state]) => state !== "error").map(([id]) => id);
      if (done.length === 0) return;
      const added = done.filter((id) => !loaded.some((i) => i.run_id === id)).length;
      refreshLimit.current = Math.min(500, Math.max(loaded.length + added, 1));
      setRefresh((n) => n + 1);
    }, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runningKey, pollTick, auth.token, pollMs]);

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
      setBackground((bs) => [
        ...bs,
        { runId: r.run_id, name: name.trim(), state: "running", disposition: null },
      ]);
      setName("");
      setDob("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
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
          <option value="kyb_officer">Company officers</option>
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

      {background.map((b) => (
        <div
          key={b.runId}
          role="status"
          data-testid="background-screening-notice"
          style={
            b.state === "running"
              ? styles.notice
              : b.state === "done"
                ? styles.noticeDone
                : styles.noticeFailed
          }
        >
          <span>
            {b.state === "running" && (
              <>
                Screening <strong>{b.name}</strong> is running in the background.
                You can keep working; the result will show here.
              </>
            )}
            {b.state === "done" && (
              <>
                Screening <strong>{b.name}</strong> finished:{" "}
                <DispositionBadge value={b.disposition} />
              </>
            )}
            {b.state === "error" && (
              <>
                Couldn't check on screening <strong>{b.name}</strong>. Reload
                the page to see whether it has finished.
              </>
            )}
            {b.state === "failed" && (
              <>
                Screening <strong>{b.name}</strong> failed. Open it for details.
              </>
            )}
          </span>
          {(b.state === "done" || b.state === "failed") && (
            <button
              type="button"
              onClick={() => onSelect(b.runId)}
              style={styles.linkBtn}
              data-testid="open-background-screening"
            >
              Open →
            </button>
          )}
          <button
            type="button"
            onClick={() => setBackground((bs) => bs.filter((x) => x.runId !== b.runId))}
            style={styles.dismiss}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}

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
                  ) : i.trigger === "kyb_officer" ? (
                    <span data-testid="officer-label" style={styles.officer}>
                      Company officer
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
  notice: { display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem",
            padding: "0.75rem 1rem", backgroundColor: "#eff6ff", border: "1px solid #93c5fd",
            borderRadius: "0.375rem", color: "#1e3a8a", fontSize: "0.85rem" },
  noticeDone: { display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem",
                padding: "0.75rem 1rem", backgroundColor: "#f0fdf4", border: "1px solid #86efac",
                borderRadius: "0.375rem", color: "#166534", fontSize: "0.85rem" },
  noticeFailed: { display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem",
                  padding: "0.75rem 1rem", backgroundColor: "#fef2f2", border: "1px solid #fca5a5",
                  borderRadius: "0.375rem", color: "#991b1b", fontSize: "0.85rem" },
  linkBtn: { background: "none", border: "none", color: "#1d4ed8", cursor: "pointer",
             fontWeight: 600, fontSize: "0.85rem", padding: 0 },
  dismiss: { marginLeft: "auto", background: "none", border: "none", color: "inherit",
             fontSize: "1.1rem", lineHeight: 1, cursor: "pointer" },
  table: { width: "100%", borderCollapse: "collapse", backgroundColor: "#fff" },
  th: { textAlign: "left", padding: "0.5rem", borderBottom: "1px solid #e5e7eb",
        fontSize: "0.75rem", color: "#6b7280", textTransform: "uppercase" },
  td: { padding: "0.5rem", borderBottom: "1px solid #f3f4f6", fontSize: "0.875rem" },
  row: { cursor: "pointer" },
  footer: { display: "flex", justifyContent: "space-between", alignItems: "center",
            marginTop: "0.75rem", fontSize: "0.875rem", color: "#6b7280" },
  officer: { backgroundColor: "#e0f2fe", color: "#075985", padding: "0.125rem 0.5rem",
             borderRadius: "9999px", fontSize: "0.75rem", fontWeight: 600 },
  monitoring: { backgroundColor: "#ede9fe", color: "#5b21b6", padding: "0.125rem 0.5rem",
                borderRadius: "9999px", fontSize: "0.75rem", fontWeight: 600 },
};
