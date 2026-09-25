/**
 * DecisionEvidencePanel: the "Why this decision?" sidebar (ticket 0069;
 * plan PLAN-IS-EVIDENCE §[3.3]).
 *
 * A right-side, non-modal drawer that walks through every decision a
 * screening run made (lists checked, blocking, each candidate's terms and
 * band, the run disposition, human actions) and cites the list entry and
 * snapshot behind each term. It fetches its own data and fails soft, like
 * ActivityPanel, so the detail page never breaks because of it.
 */

import { useEffect, useId, useRef, useState } from "react";
import {
  apiClient,
  ExplanationStep,
  ScreeningCitation,
  ScreeningExplanation,
} from "../api/client";
import { DispositionBadge } from "./DispositionBadge";

type Step<K extends ExplanationStep["kind"]> = Extract<ExplanationStep, { kind: K }>;

function step<K extends ExplanationStep["kind"]>(e: ScreeningExplanation, kind: K): Step<K> | undefined {
  return e.steps.find((s) => s.kind === kind) as Step<K> | undefined;
}

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "unknown date";
}

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function citationText(c: ScreeningCitation): string {
  if (c.about === "subject") return `Subject submission · ${c.field ?? "field"}`;
  return `${c.display_name} · entry ${c.entry_id ?? "?"} · ${c.field} · list as of ${day(c.snapshot_retrieved_at)}`;
}

function citationLabel(c: ScreeningCitation): string {
  if (c.about === "subject") return `Evidence: subject submission, field ${c.field ?? "unknown"}`;
  return (
    `Evidence: ${c.display_name}, entry ${c.entry_id ?? "unknown"}, field ${c.field}, ` +
    `list as of ${day(c.snapshot_retrieved_at)}. Show details`
  );
}

function CitationChip({ id, citation }: { id: string; citation: ScreeningCitation }) {
  const [open, setOpen] = useState(false);
  if (citation.about === "subject") {
    return (
      <span data-testid={`citation-${id}`} aria-label={citationLabel(citation)} style={styles.chipSubject}>
        {citationText(citation)}
      </span>
    );
  }
  return (
    <span style={styles.chipWrap}>
      <button
        type="button"
        data-testid={`citation-${id}`}
        aria-label={citationLabel(citation)}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={styles.chip}
      >
        {citationText(citation)}
      </button>
      {open && (
        <span data-testid={`citation-detail-${id}`} style={styles.chipDetail}>
          <span>
            Snapshot <code>{citation.snapshot_id ?? "—"}</code>
            {citation.content_sha256 && (
              <>
                {" "}· SHA-256 <code title={citation.content_sha256}>{citation.content_sha256.slice(0, 12)}</code>{" "}
                <button
                  type="button"
                  style={styles.linkBtn}
                  onClick={() => void navigator.clipboard?.writeText(citation.content_sha256!)}
                >
                  copy
                </button>
              </>
            )}
          </span>
          <span>
            Locator <code>{citation.locator}</code>
          </span>
          {citation.source_url && (
            <a href={citation.source_url} target="_blank" rel="noreferrer">
              Open the published list file
            </a>
          )}
        </span>
      )}
    </span>
  );
}

function Explanation({ data, focus }: { data: ScreeningExplanation; focus: string | null }) {
  const sources = step(data, "sources");
  const blocking = step(data, "blocking");
  const scoring = step(data, "scoring");
  const banding = step(data, "banding");
  const disposition = step(data, "disposition");
  const human = step(data, "human");
  const bandBy = new Map((banding?.candidates ?? []).map((c) => [c.candidate_id, c]));
  const drift =
    (banding?.candidates ?? []).some((c) => !c.consistent) || disposition?.consistent === false;

  useEffect(() => {
    if (focus) document.getElementById(`why-cand-${focus}`)?.scrollIntoView?.({ block: "start" });
  }, [focus, data]);

  return (
    <>
      {data.subject_shredded && (
        <div data-testid="why-shredded" style={styles.note}>
          Subject data has been shredded under the retention policy. The list evidence and the
          decision trail are still shown.
        </div>
      )}
      {drift && (
        <div data-testid="why-drift" style={styles.warn}>
          Explained with today's rules, which disagree with how this decision was made (rule v
          {data.rule_version}, normalizer {data.normalizer_version}). Replay the decision to check it.
        </div>
      )}

      <ol style={styles.steps}>
        {sources && (
          <li data-testid="why-step-sources" style={styles.step}>
            <h3 style={styles.stepTitle}>Lists checked</h3>
            <ul style={styles.plain}>
              {sources.lists.map((l) => (
                <li key={l.source} style={styles.row}>
                  <strong>{l.display_name}</strong>{" "}
                  <span style={l.status === "complete" ? styles.ok : styles.bad}>{l.status}</span>
                  <div style={styles.muted}>
                    {l.snapshot_id
                      ? `as of ${day(l.retrieved_at)} · ${l.record_count ?? "?"} people · SHA-256 ${
                          l.content_sha256?.slice(0, 12) ?? "—"
                        }`
                      : `no snapshot within ${sources.max_age_days} days`}
                  </div>
                </li>
              ))}
            </ul>
          </li>
        )}

        {blocking && (
          <li data-testid="why-step-blocking" style={styles.step}>
            <h3 style={styles.stepTitle}>Candidates found</h3>
            {blocking.candidate_count === 0 ? (
              <p style={styles.p}>No list record shared a name key with the subject.</p>
            ) : (
              <p style={styles.p}>
                {blocking.candidate_count} candidate{blocking.candidate_count === 1 ? "" : "s"} shared at
                least one name key with the subject. Records that match every part of the name are
                never capped; partial matches are capped at {blocking.cap}.
              </p>
            )}
          </li>
        )}

        {scoring && scoring.candidates.length > 0 && (
          <li data-testid="why-step-candidates" style={styles.step}>
            <h3 style={styles.stepTitle}>How each candidate scored</h3>
            {banding && (
              <p style={styles.muted}>
                Thresholds: CLEAR below {banding.thresholds.clear_below}, MATCH at{" "}
                {banding.thresholds.match_at} or above.
              </p>
            )}
            {scoring.candidates.map((c) => {
              const band = bandBy.get(c.candidate_id);
              return (
                <section
                  key={c.candidate_id}
                  id={`why-cand-${c.candidate_id}`}
                  data-testid={`why-cand-${c.candidate_id}`}
                  data-focused={focus === c.candidate_id ? "true" : "false"}
                  style={focus === c.candidate_id ? { ...styles.cand, ...styles.candFocused } : styles.cand}
                >
                  <div style={styles.candHead}>
                    {band && <DispositionBadge value={band.band} />}
                    <strong>{c.record_ref.primary_name}</strong>
                    <span style={styles.muted}>
                      {c.record_ref.display_name} · entry {c.record_ref.entry_id}
                      {c.record_ref.program ? ` · ${c.record_ref.program}` : ""}
                    </span>
                  </div>
                  {band && <p style={styles.p}>{band.reason_text}</p>}
                  <ul style={styles.plain}>
                    {c.terms.map((t) => (
                      <li
                        key={t.name}
                        data-testid={`why-term-${c.candidate_id}-${t.name}`}
                        data-tone={t.weight < 0 ? "conflict" : "support"}
                        style={{ ...styles.term, borderLeftColor: t.weight < 0 ? "#dc2626" : "#16a34a" }}
                      >
                        <div>
                          {t.label}{" "}
                          <span style={styles.weight}>{t.weight > 0 ? `+${t.weight}` : t.weight}</span>
                        </div>
                        <div style={styles.muted}>List value: {fmt(t.record_value)}</div>
                        <div style={styles.chips}>
                          {t.citations
                            .filter((id) => data.citations[id])
                            .map((id) => (
                              <CitationChip key={id} id={id} citation={data.citations[id]} />
                            ))}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </li>
        )}

        {disposition && (
          <li data-testid="why-step-disposition" style={styles.step}>
            <h3 style={styles.stepTitle}>
              Decision <DispositionBadge value={disposition.system_disposition} auto={disposition.auto_closed} />
            </h3>
            <p style={styles.p}>{disposition.reason_text}</p>
            {disposition.coverage_gaps.length > 0 && (
              <p style={styles.muted}>Coverage gaps: {disposition.coverage_gaps.join(", ")}</p>
            )}
            {disposition.common_name && (
              <p style={styles.muted}>
                Common name: {disposition.common_name.frequency} full-name matches (threshold{" "}
                {disposition.common_name.threshold}), so uncorroborated name matches were penalized.
              </p>
            )}
          </li>
        )}

        {human && (
          <li data-testid="why-step-human" style={styles.step}>
            <h3 style={styles.stepTitle}>Human review</h3>
            {human.dispositions.length === 0 && human.replays.length === 0 && (
              <p style={styles.muted}>No human action yet.</p>
            )}
            <ul style={styles.plain}>
              {human.dispositions.map((d, i) => (
                <li key={`d${i}`} style={styles.row}>
                  <strong>{d.disposition}</strong> on {day(d.created_at)}
                  {d.notes ? `: ${d.notes}` : ""}
                </li>
              ))}
              {human.replays.map((r, i) => (
                <li key={`r${i}`} style={styles.row}>
                  Replay on {day(r.occurred_at)}:{" "}
                  {r.shredded
                    ? "subject shredded, can't replay"
                    : r.reproduced
                      ? "Reproduced the original verdict"
                      : "did not reproduce the original verdict"}
                </li>
              ))}
            </ul>
          </li>
        )}
      </ol>
    </>
  );
}

export function DecisionEvidencePanel({
  runId,
  token,
  decided,
  focusCandidateId,
  refreshKey,
  onClose,
}: {
  runId: string;
  token: string;
  /** False while the run is in flight: nothing to explain yet. */
  decided: boolean;
  focusCandidateId: string | null;
  /** Changes when the page refreshes, so the panel refreshes with it. */
  refreshKey: number;
  onClose: () => void;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [data, setData] = useState<ScreeningExplanation | null>(null);
  const [error, setError] = useState(false);

  // Focus moves into the panel and returns to whatever opened it.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!decided) return;
    let cancelled = false;
    apiClient
      .getScreeningExplanation(runId, token)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(false);
        }
      })
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [runId, token, decided, refreshKey]);

  return (
    <aside role="dialog" aria-modal="false" aria-labelledby={titleId} style={styles.drawer}>
      <header style={styles.header}>
        <h2 id={titleId} style={styles.title}>Why this decision?</h2>
        <button ref={closeRef} type="button" onClick={onClose} style={styles.close} aria-label="Close">
          ✕
        </button>
      </header>
      <div style={styles.body}>
        {!decided ? (
          <p data-testid="why-pending" style={styles.muted}>
            The decision is not made yet. This panel fills in when screening finishes.
          </p>
        ) : error ? (
          <p data-testid="why-error" style={styles.muted}>
            Decision explanation is unavailable right now.
          </p>
        ) : data === null ? (
          <p style={styles.muted}>Loading explanation…</p>
        ) : (
          <Explanation data={data} focus={focusCandidateId} />
        )}
      </div>
    </aside>
  );
}

const styles: Record<string, React.CSSProperties> = {
  drawer: {
    position: "fixed", top: 0, right: 0, bottom: 0, width: "min(440px, 100vw)", zIndex: 20,
    background: "#fff", boxShadow: "-4px 0 16px rgba(0,0,0,0.12)", display: "flex",
    flexDirection: "column",
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "0.75rem 1rem", borderBottom: "1px solid #e5e7eb",
  },
  title: { margin: 0, fontSize: "1.05rem", color: "#111827" },
  close: {
    background: "none", border: "1px solid #d1d5db", borderRadius: "0.375rem",
    padding: "0.125rem 0.5rem", cursor: "pointer", fontSize: "0.9rem",
  },
  body: { overflowY: "auto", padding: "0.75rem 1rem 2rem", flex: 1 },
  steps: { margin: 0, paddingLeft: "1.25rem" },
  step: { marginBottom: "1.25rem" },
  stepTitle: { fontSize: "0.95rem", margin: "0 0 0.375rem", color: "#111827", display: "flex",
               gap: "0.5rem", alignItems: "center" },
  p: { fontSize: "0.85rem", margin: "0 0 0.375rem", color: "#1f2937" },
  plain: { listStyle: "none", padding: 0, margin: 0 },
  row: { fontSize: "0.85rem", marginBottom: "0.375rem" },
  muted: { color: "#6b7280", fontSize: "0.8rem", margin: "0 0 0.25rem" },
  ok: { color: "#166534", fontSize: "0.75rem" },
  bad: { color: "#991b1b", fontSize: "0.75rem", fontWeight: 600 },
  cand: { border: "1px solid #e5e7eb", borderRadius: "0.5rem", padding: "0.5rem 0.625rem",
          marginBottom: "0.625rem" },
  candFocused: { borderColor: "#2563eb", boxShadow: "0 0 0 2px #bfdbfe" },
  candHead: { display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center",
              marginBottom: "0.25rem" },
  term: { borderLeft: "3px solid", padding: "0.25rem 0.5rem", marginBottom: "0.375rem",
          fontSize: "0.85rem" },
  weight: { fontWeight: 600 },
  chips: { display: "flex", flexWrap: "wrap", gap: "0.25rem", marginTop: "0.25rem" },
  chipWrap: { display: "inline-flex", flexDirection: "column", gap: "0.25rem", maxWidth: "100%" },
  chip: { background: "#eff6ff", color: "#1e3a8a", border: "1px solid #bfdbfe",
          borderRadius: "999px", padding: "0.125rem 0.5rem", fontSize: "0.72rem", cursor: "pointer",
          textAlign: "left" },
  chipSubject: { background: "#f3f4f6", color: "#374151", borderRadius: "999px",
                 padding: "0.125rem 0.5rem", fontSize: "0.72rem" },
  chipDetail: { display: "flex", flexDirection: "column", gap: "0.125rem", fontSize: "0.72rem",
                color: "#374151", background: "#f9fafb", border: "1px solid #e5e7eb",
                borderRadius: "0.375rem", padding: "0.375rem 0.5rem", wordBreak: "break-all" },
  linkBtn: { background: "none", border: "none", color: "#1d4ed8", textDecoration: "underline",
             cursor: "pointer", padding: 0, fontSize: "inherit" },
  note: { background: "#f3f4f6", color: "#374151", padding: "0.5rem 0.75rem",
          borderRadius: "0.375rem", marginBottom: "0.75rem", fontSize: "0.8rem" },
  warn: { background: "#fef3c7", color: "#92400e", padding: "0.5rem 0.75rem",
          borderRadius: "0.375rem", marginBottom: "0.75rem", fontSize: "0.8rem" },
};
