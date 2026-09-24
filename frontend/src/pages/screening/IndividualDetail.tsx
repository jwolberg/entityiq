/**
 * Individual screening detail (ticket 0043; PRD-IDV §[16], N2).
 *
 * Subject vs each candidate side by side, every scoring term with the claims
 * it cites (source + locator), conflicts shown beside matches, list snapshot
 * and rule version, coverage gaps, disposition form (operators/leads), and
 * replay (leads/examiners). Polls while the run is still in flight.
 */

import { useEffect, useState } from "react";
import {
  apiClient,
  ReplayResult,
  ScreeningCandidateView,
  ScreeningDetail,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { DispositionBadge } from "../../components/DispositionBadge";

export const SCREENING_POLL_MS = 3000;

const CONFLICT_TERMS = new Set([
  "dob_conflict",
  "id_number_conflict",
  "nationality_conflict",
  "common_name_penalty",
]);

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Candidate({ cand, subject }: { cand: ScreeningCandidateView; subject: Record<string, unknown> | null }) {
  return (
    <div data-testid={`candidate-${cand.candidate_id}`} style={styles.card}>
      <div style={styles.cardHeader}>
        <DispositionBadge value={cand.band} />
        <span style={styles.score}>score {cand.score.toFixed(2)}</span>
        <span style={styles.muted}>
          {cand.record.source}:{cand.record.source_entry_id}
          {cand.record.program ? ` · ${cand.record.program}` : ""}
        </span>
      </div>
      <div style={styles.sideBySide}>
        <div>
          <div style={styles.colTitle}>Subject</div>
          <div>{fmt(subject?.name)}</div>
          <div style={styles.muted}>DOB {fmt(subject?.dob)} · {fmt(subject?.nationality)}</div>
        </div>
        <div>
          <div style={styles.colTitle}>List record</div>
          <div data-testid="record-name">{cand.record.primary_name}</div>
          <div style={styles.muted}>
            DOB {fmt(cand.record.dobs)} · {cand.record.nationalities.join(", ") || "—"}
          </div>
        </div>
      </div>
      <ul style={styles.terms}>
        {cand.terms.map((t) => {
          const conflict = CONFLICT_TERMS.has(t.name) || t.weight < 0;
          return (
            <li
              key={t.name}
              data-testid={`term-${t.name}`}
              data-tone={conflict ? "conflict" : "support"}
              style={{ ...styles.term, borderLeftColor: conflict ? "#dc2626" : "#16a34a" }}
            >
              <strong>{t.name}</strong>{" "}
              <span>{t.weight > 0 ? `+${t.weight}` : t.weight}</span>
              <div style={styles.muted}>list value: {fmt(t.record_value)}</div>
              {t.claim_ids
                .map((id) => cand.claims[id])
                .filter(Boolean)
                .map((c) => (
                  <div key={c.locator} style={styles.claim}>
                    {c.locator}
                  </div>
                ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function IndividualDetail({
  runId,
  onBack,
  onOpenRun,
}: {
  runId: string;
  onBack: () => void;
  onOpenRun?: (runId: string) => void;
}) {
  const { auth } = useAuth();
  const [data, setData] = useState<ScreeningDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [replay, setReplay] = useState<ReplayResult | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .getScreening(runId, auth.token)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [runId, auth.token, tick]);

  const inFlight = data !== null && (data.run.status === "pending" || data.run.status === "running");
  useEffect(() => {
    if (!inFlight) return;
    const t = setTimeout(() => setTick((n) => n + 1), SCREENING_POLL_MS);
    return () => clearTimeout(t);
  }, [inFlight, data]);

  async function dispose(disposition: "CLEAR" | "MATCH") {
    setBusy(true);
    try {
      await apiClient.disposeScreening(runId, { disposition, notes: notes || undefined }, auth.token);
      setNotes("");
      setTick((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runReplay() {
    try {
      setReplay(await apiClient.replayScreening(runId, auth.token));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const canDispose = auth.role === "operator" || auth.role === "lead";
  const canReplay = auth.role === "lead" || auth.role === "examiner";
  const gaps = data
    ? Object.entries(data.run.stages).filter(([, s]) => s === "unavailable").map(([n]) => n)
    : [];

  return (
    <div>
      <button onClick={onBack} style={styles.back}>← Individuals</button>
      {error && <div role="alert" style={styles.error}>{error}</div>}
      {data === null && !error && <div>Loading…</div>}
      {data && (
        <>
          <h2 style={styles.heading}>
            {data.subject_shredded ? "(subject data shredded)" : fmt(data.subject?.name)}{" "}
            <DispositionBadge value={data.decision?.system_disposition ?? null} auto={data.decision?.auto_closed} />
          </h2>

          {data.monitoring && (
            <div data-testid="monitoring-banner" style={styles.monitoring}>
              Monitoring alert: list <strong>{data.monitoring.source}</strong> changed (snapshot{" "}
              {data.monitoring.snapshot_id}). Changed entries:{" "}
              {data.monitoring.changed_entry_ids.join(", ") || "none among candidates"}.
              {data.monitoring.prior_run_id && (
                <>
                  {" "}Previous decision: {data.monitoring.prior_disposition ?? "none"}.{" "}
                  <button
                    data-testid="open-prior-run"
                    style={styles.linkBtn}
                    onClick={() => onOpenRun?.(data.monitoring!.prior_run_id!)}
                  >
                    Open previous screening
                  </button>
                </>
              )}
            </div>
          )}

          {inFlight && (
            <div data-testid="screening-in-progress" style={styles.muted}>
              Screening in progress: updating automatically.
            </div>
          )}

          {data.decision && (
            <div data-testid="decision-meta" style={styles.meta}>
              rule v{data.decision.rule_version} · normalizer {data.decision.normalizer_version} ·
              thresholds clear &lt; {data.decision.thresholds.clear_below}, match ≥{" "}
              {data.decision.thresholds.match_at} · lists {data.decision.snapshot_ids.join(", ") || "none"}
              {data.run.duration_seconds != null && ` · took ${data.run.duration_seconds.toFixed(1)}s`}
            </div>
          )}

          {gaps.length > 0 && (
            <div data-testid="coverage-gaps" style={styles.gaps}>
              Coverage gaps (sources unavailable): {gaps.join(", ")}. A CLEAR can't auto-close
              while a source is missing.
            </div>
          )}

          {data.candidates.length === 0 && !inFlight && (
            <div style={styles.muted}>No list candidates matched this subject.</div>
          )}
          {data.candidates.map((c) => (
            <Candidate key={c.candidate_id} cand={c} subject={data.subject} />
          ))}

          <section style={styles.section}>
            <h3 style={styles.sub}>Dispositions</h3>
            <ul data-testid="disposition-history" style={styles.history}>
              {data.dispositions.map((d, i) => (
                <li key={i}>
                  <strong>{d.disposition}</strong> {d.notes ? `: ${d.notes}` : ""}
                </li>
              ))}
              {data.dispositions.length === 0 && <li style={styles.muted}>None yet.</li>}
            </ul>

            {canDispose && data.decision && (
              <div data-testid="disposition-form" style={styles.form}>
                <textarea
                  data-testid="disposition-notes"
                  placeholder="Notes (why)"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  style={styles.textarea}
                />
                <button data-testid="dispose-CLEAR" disabled={busy} onClick={() => dispose("CLEAR")} style={styles.clearBtn}>
                  Not this person (CLEAR)
                </button>
                <button data-testid="dispose-MATCH" disabled={busy} onClick={() => dispose("MATCH")} style={styles.matchBtn}>
                  Confirmed match (MATCH)
                </button>
              </div>
            )}

            {canReplay && data.decision && (
              <div style={styles.form}>
                <button data-testid="replay-button" onClick={runReplay} style={styles.back}>
                  Replay decision
                </button>
                {replay && (
                  <span data-testid="replay-result">
                    {replay.shredded
                      ? "Subject shredded: can't replay"
                      : replay.reproduced
                        ? "Reproduced the original verdict"
                        : `Mismatch: ${replay.differences.map((d) => d.field).join(", ")}`}
                  </span>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  back: { background: "none", border: "1px solid #d1d5db", borderRadius: "0.375rem",
          padding: "0.25rem 0.625rem", cursor: "pointer", marginBottom: "0.75rem" },
  heading: { margin: "0.5rem 0", fontSize: "1.25rem", color: "#111827" },
  meta: { fontSize: "0.8rem", color: "#4b5563", marginBottom: "0.75rem" },
  gaps: { background: "#fef3c7", color: "#92400e", padding: "0.5rem 0.75rem",
          borderRadius: "0.375rem", marginBottom: "0.75rem", fontSize: "0.85rem" },
  card: { background: "#fff", borderRadius: "0.5rem", padding: "0.75rem 1rem", marginBottom: "0.75rem",
          boxShadow: "0 1px 2px rgba(0,0,0,0.06)" },
  cardHeader: { display: "flex", gap: "0.75rem", alignItems: "center", marginBottom: "0.5rem" },
  score: { fontWeight: 600, fontSize: "0.85rem" },
  sideBySide: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "0.5rem" },
  colTitle: { fontSize: "0.7rem", textTransform: "uppercase", color: "#6b7280" },
  terms: { listStyle: "none", padding: 0, margin: 0 },
  term: { borderLeft: "3px solid", padding: "0.25rem 0.5rem", marginBottom: "0.375rem", fontSize: "0.85rem" },
  claim: { fontFamily: "monospace", fontSize: "0.75rem", color: "#6b7280" },
  muted: { color: "#6b7280", fontSize: "0.8rem" },
  section: { marginTop: "1rem" },
  sub: { fontSize: "1rem", margin: "0 0 0.5rem" },
  history: { margin: "0 0 0.75rem", paddingLeft: "1.25rem" },
  form: { display: "flex", gap: "0.5rem", alignItems: "flex-start", marginBottom: "0.75rem" },
  textarea: { flex: 1, minHeight: "3rem", padding: "0.375rem", borderRadius: "0.375rem",
              border: "1px solid #d1d5db" },
  clearBtn: { padding: "0.375rem 0.75rem", borderRadius: "0.375rem", border: "none",
              background: "#166534", color: "#fff", cursor: "pointer" },
  matchBtn: { padding: "0.375rem 0.75rem", borderRadius: "0.375rem", border: "none",
              background: "#991b1b", color: "#fff", cursor: "pointer" },
  error: { color: "#991b1b", marginBottom: "0.75rem" },
  monitoring: { background: "#ede9fe", color: "#4c1d95", padding: "0.5rem 0.75rem",
                borderRadius: "0.375rem", marginBottom: "0.75rem", fontSize: "0.85rem" },
  linkBtn: { background: "none", border: "none", color: "#4c1d95", textDecoration: "underline",
             cursor: "pointer", padding: 0 },
};
