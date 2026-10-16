/**
 * PublicRecordsPanel — federal court dockets (CourtListener) and SAM.gov
 * exclusions for a company (ticket 0095; backend ticket 0092).
 *
 * Reads `public_records` evidence from GET /reports/{run_id}:
 *   - public_records_status  one per source (searched / no_records / stale /
 *                            unavailable / timeout / rate_limited / not_applicable)
 *   - public_records_summary true totals per kind
 *   - public_record          one per docket or exclusion, detail in raw_payload
 *
 * Strong matches are grouped by kind, exclusions first. Weak matches sit
 * under "Possible matches" and never score. A record that a risk signal cites
 * is marked "Counts toward risk"; the scoring rule itself lives in the backend.
 * "No records" never reads as a clean result: coverage is partial.
 */

import type React from "react";
import type { ContributingSignal, EvidenceItem } from "../api/client";

interface PublicRecordsPanelProps {
  evidence: EvidenceItem[];
  status: string;
  signals: ContributingSignal[];
}

type Payload = Record<string, unknown>;

const SOURCE = "public_records";
const KIND_ORDER = ["federal_exclusion", "bankruptcy", "federal_civil"] as const;

const KIND_LABEL: Record<string, string> = {
  federal_exclusion: "Federal exclusions (SAM.gov)",
  bankruptcy: "Federal bankruptcies",
  federal_civil: "Open federal civil cases",
};

const SOURCE_LABEL: Record<string, string> = {
  courtlistener: "Federal court dockets (CourtListener)",
  sam_exclusions: "Federal exclusions (SAM.gov)",
};

const LINK_HOSTS = new Set(["www.courtlistener.com", "courtlistener.com", "sam.gov", "www.sam.gov"]);

const COVERAGE_NOTE =
  "Only federal bankruptcy and civil dockets on CourtListener and active SAM.gov federal exclusions are searched. State courts, liens and UCC filings are not covered, so finding nothing is not a clean result.";

function text(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

/** Only https links on the known hosts become anchors; anything else is text. */
function safeUrl(value: unknown): string | null {
  const raw = text(value);
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && LINK_HOSTS.has(url.hostname) ? url.toString() : null;
  } catch {
    return null;
  }
}

function statusText(value: string, payload: Payload): string {
  switch (value) {
    case "searched":
      return "Searched — matches listed below.";
    case "no_records":
      return "Searched — no records found.";
    case "stale": {
      const date = text(payload.snapshot_retrieved_at)?.slice(0, 10) ?? "an unknown date";
      return `Searched a stale list (last loaded ${date}) — refresh the SAM extract.`;
    }
    case "timeout":
    case "rate_limited":
      return "Could not be checked (temporary) — re-run later.";
    case "not_applicable":
      return "Not applicable (US companies only).";
    case "unavailable":
    default:
      return "Not available (source not configured or down).";
  }
}

function payloadOf(item: EvidenceItem): Payload {
  return (item.raw_payload ?? {}) as Payload;
}

function RecordRow({ item, scoredIds }: { item: EvidenceItem; scoredIds: Set<string> }) {
  const p = payloadOf(item);
  const kind = text(p.kind) ?? item.raw_value ?? "";
  const url = safeUrl(p.source_url ?? item.attribution?.["source_url"]);
  const reference = text(p.docket_number) ?? text(p.sam_number) ?? "record";
  const counts = scoredIds.has(item.id);
  const notScored = !counts && (p.match !== "strong" || kind === "federal_civil" || item.normalized_value === "unscored");

  const details: string[] = [];
  if (kind === "federal_exclusion") {
    details.push(`${text(p.agency) ?? "Agency unknown"} — ${text(p.exclusion_type) ?? "type not given"}`);
    details.push(`Active since ${text(p.active_date) ?? "unknown"}; ends ${text(p.termination_date) ?? "unknown"}`);
    details.push(`Status: ${text(p.status) ?? "unknown"}${p.classification ? ` · ${String(p.classification)}` : ""}`);
    details.push(`SAM number ${reference}`);
  } else {
    if (kind === "bankruptcy" && p.chapter) details.push(`Chapter ${String(p.chapter)}`);
    details.push(text(p.court) ?? "Court unknown");
    details.push(`Filed ${text(p.filed_date) ?? "unknown"}${p.terminated_date ? `; closed ${String(p.terminated_date)}` : "; open"}`);
    if (kind === "federal_civil") details.push("Role unknown");
    if (p.out_of_date || p.freshness === "unknown" || p.freshness === "not_checked") {
      details.push("Status may be out of date");
    }
  }

  return (
    <li style={styles.record} data-testid={`public-record-${item.id}`}>
      <div style={styles.recordHead}>
        <span style={styles.party}>{text(p.party_name) ?? "Unnamed party"}</span>
        {counts && <span style={{ ...styles.badge, ...styles.badgeRisk }}>Counts toward risk</span>}
        {notScored && <span style={styles.badge}>Not scored</span>}
      </div>
      <div style={styles.detail}>{details.join(" · ")}</div>
      <div style={styles.detail}>
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer">
            {`View docket ${reference} on CourtListener`}
          </a>
        ) : (
          <span>{kind === "federal_exclusion" ? `SAM.gov record ${reference}` : `Docket ${reference}`}</span>
        )}
      </div>
    </li>
  );
}

export function PublicRecordsPanel({ evidence, status, signals }: PublicRecordsPanelProps) {
  if (status === "pending") {
    return (
      <div style={styles.muted} data-testid="public-records-pending">
        Still being gathered — check back shortly.
      </div>
    );
  }
  const rows = evidence.filter((e) => e.source === SOURCE);
  const statuses = rows.filter((e) => e.field === "public_records_status");
  if (rows.length === 0) {
    return (
      <div style={styles.muted} data-testid="public-records-not-run">
        Public-records check did not run for this report.
      </div>
    );
  }
  const summary = payloadOf(rows.find((e) => e.field === "public_records_summary") ?? ({} as EvidenceItem));
  const truncated = statuses.some((s) => payloadOf(s).truncated === true);
  const records = rows.filter((e) => e.field === "public_record");
  const scoredIds = new Set(signals.flatMap((s) => s.evidence_ids ?? []));
  const isStrong = (e: EvidenceItem) => payloadOf(e).match === "strong";
  const possible = records.filter((e) => !isStrong(e));

  return (
    <div data-testid="public-records-panel">
      <ul style={styles.list}>
        {statuses.map((s) => {
          const source = text(payloadOf(s).source) ?? "unknown";
          return (
            <li key={s.id} style={styles.status} data-testid={`public-records-status-${source}`}>
              <strong>{SOURCE_LABEL[source] ?? source}:</strong> {statusText(s.raw_value ?? "", payloadOf(s))}
            </li>
          );
        })}
      </ul>

      {KIND_ORDER.map((kind) => {
        const items = records.filter((e) => e.raw_value === kind && isStrong(e));
        if (items.length === 0) return null;
        const counts = (summary[kind] ?? {}) as Payload;
        const total = typeof counts.total === "number" ? counts.total : null;
        const showTotal = truncated && total !== null && total > items.length;
        return (
          <section key={kind} data-testid={`public-records-group-${kind}`} style={styles.group}>
            <h4 style={styles.heading}>
              {KIND_LABEL[kind]}
              {kind === "federal_civil" && <span style={styles.badge}>Not scored</span>}
            </h4>
            {showTotal && (
              <div style={styles.detail}>{`Showing ${items.length} of ${total} — the most recent first.`}</div>
            )}
            <ul style={styles.list}>
              {items.map((item) => (
                <RecordRow key={item.id} item={item} scoredIds={scoredIds} />
              ))}
            </ul>
          </section>
        );
      })}

      {possible.length > 0 && (
        <section data-testid="public-records-possible" style={styles.group}>
          <h4 style={styles.heading}>Possible matches (not scored)</h4>
          <ul style={styles.list}>
            {possible.map((item) => (
              <RecordRow key={item.id} item={item} scoredIds={scoredIds} />
            ))}
          </ul>
        </section>
      )}

      <p style={styles.coverage} data-testid="public-records-coverage">
        {COVERAGE_NOTE}
      </p>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  list: { listStyle: "none", margin: 0, padding: 0 },
  status: { fontSize: "0.85rem", color: "#374151", margin: "0.15rem 0" },
  group: { marginTop: "0.75rem" },
  heading: { margin: "0 0 0.25rem", fontSize: "0.85rem", fontWeight: 600, color: "#374151" },
  record: { padding: "0.4rem 0", borderTop: "1px solid #e5e7eb" },
  recordHead: { display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" },
  party: { fontWeight: 500 },
  detail: { fontSize: "0.8rem", color: "#4b5563" },
  badge: {
    fontSize: "0.7rem",
    padding: "0.05rem 0.4rem",
    borderRadius: "999px",
    background: "#f3f4f6",
    color: "#374151",
    marginLeft: "0.4rem",
  },
  badgeRisk: { background: "#fef3c7", color: "#92400e" },
  muted: { fontSize: "0.85rem", color: "#6b7280" },
  coverage: { fontSize: "0.75rem", color: "#6b7280", marginTop: "0.75rem" },
};
