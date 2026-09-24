/**
 * DetailPanels — read-only Company Detail panels (P2-T8).
 *
 * Renders the four PRD § Company Detail View panels from data already
 * exposed by GET /reports/{run_id}:
 *   - DomainPanel          (Tier-2 "domain" evidence + infrastructure score)
 *   - RegistryPanel        (Tier-1 "opencorporates" evidence)
 *   - ContactPanel         (Tier-3 "web" contact evidence + source attribution)
 *   - RiskAssessmentPanel  (four-layer scores, triage tier, risk flags, summary)
 *
 * Each panel is a CONTENT component: CompanyDetail wraps it in a <section>
 * with a heading.  Panels handle the three partial-result states themselves:
 *   - status "pending"      → "still computing" notice
 *   - no relevant evidence  → "not available" notice
 *   - otherwise             → field rows
 *
 * No backend changes are required for this ticket.
 */

import { EvidenceItem, ScoresData, SourceSummary } from "../api/client";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Find the first evidence row for a field (optionally restricted to a source). */
function findEvidence(
  evidence: EvidenceItem[],
  field: string,
  source?: string
): EvidenceItem | undefined {
  return evidence.find(
    (e) => e.field === field && (source === undefined || e.source === source)
  );
}

/** Preferred display value for an evidence row (normalized, falling back to raw). */
function evValue(item: EvidenceItem | undefined): string | null {
  if (!item) return null;
  return item.normalized_value ?? item.raw_value ?? null;
}

interface Row {
  label: string;
  value: string | null;
  /** Optional small badge shown after the value (e.g. a risk flag). */
  flag?: { text: string; tone: "warn" | "ok" };
  /** Optional attribution line shown under the value. */
  attribution?: string | null;
}

function FieldRow({ row }: { row: Row }) {
  return (
    <div style={styles.row} data-testid={`field-${row.label.replace(/\s+/g, "-").toLowerCase()}`}>
      <div style={styles.rowLabel}>{row.label}</div>
      <div style={styles.rowValue}>
        <span>{row.value ?? "—"}</span>
        {row.flag && (
          <span
            style={{
              ...styles.flag,
              ...(row.flag.tone === "warn" ? styles.flagWarn : styles.flagOk),
            }}
          >
            {row.flag.text}
          </span>
        )}
        {row.attribution && (
          <div style={styles.attribution}>source: {row.attribution}</div>
        )}
      </div>
    </div>
  );
}

function Pending({ testId }: { testId: string }) {
  return (
    <div style={styles.pending} data-testid={testId}>
      Still being gathered — check back shortly.
    </div>
  );
}

function NotAvailable({ label, testId }: { label: string; testId: string }) {
  return (
    <div style={styles.empty} data-testid={testId}>
      {label}
    </div>
  );
}

function FieldRows({ rows }: { rows: Row[] }) {
  return (
    <div style={styles.rows}>
      {rows.map((r) => (
        <FieldRow key={r.label} row={r} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// HQ Visualization (P2-T9) — static OpenStreetMap tile map, no key or library
// ---------------------------------------------------------------------------

const CONFIDENCE_LABEL: Record<string, { text: string; tone: "ok" | "warn" }> = {
  high: { text: "High — registry and submission agree", tone: "ok" },
  medium: { text: "Medium — registry address only", tone: "ok" },
  low: { text: "Low — self-reported or conflicting", tone: "warn" },
};

const TILE = 256;
const ZOOM = 15;

/** Web-Mercator tile coordinates (fractional) for a lat/lon at ZOOM. */
function tileCoords(lat: number, lon: number): { x: number; y: number } {
  const n = 2 ** ZOOM;
  const r = (lat * Math.PI) / 180;
  return {
    x: ((lon + 180) / 360) * n,
    y: ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n,
  };
}

/**
 * 3×3 grid of OSM tiles positioned so the point sits at the frame centre.
 * Plain <img> tiles (not an iframe embed) render everywhere, including
 * headless screenshots, and need no JS map library.
 */
function StaticMap({ lat, lon }: { lat: number; lon: number }) {
  const { x, y } = tileCoords(lat, lon);
  const tx = Math.floor(x);
  const ty = Math.floor(y);
  const offsetX = TILE + (x - tx) * TILE; // point's px position inside the grid
  const offsetY = TILE + (y - ty) * TILE;
  const tiles = [];
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      tiles.push(
        <img
          key={`${dx},${dy}`}
          src={`https://tile.openstreetmap.org/${ZOOM}/${tx + dx}/${ty + dy}.png`}
          alt=""
          width={TILE}
          height={TILE}
          style={{ position: "absolute", left: (dx + 1) * TILE, top: (dy + 1) * TILE }}
        />
      );
    }
  }
  return (
    <div style={styles.map} data-testid="hq-map" role="img" aria-label="Headquarters location map">
      <div
        style={{
          position: "absolute",
          left: `calc(50% - ${offsetX}px)`,
          top: `calc(50% - ${offsetY}px)`,
          width: TILE * 3,
          height: TILE * 3,
        }}
      >
        {tiles}
      </div>
      <div style={styles.marker} data-testid="hq-marker" />
      <div style={styles.mapAttribution}>
        ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap
        </a>{" "}
        contributors
      </div>
    </div>
  );
}

interface HqPanelProps {
  evidence: EvidenceItem[];
  status: string; // section_statuses.evidence
}

export function HqPanel({ evidence, status }: HqPanelProps) {
  if (status === "pending") return <Pending testId="hq-pending" />;
  const get = (f: string) => evValue(findEvidence(evidence, f, "geocode"));
  const lat = Number(get("hq_latitude"));
  const lon = Number(get("hq_longitude"));
  if (!get("hq_latitude") || Number.isNaN(lat) || Number.isNaN(lon)) {
    return (
      <NotAvailable
        label="HQ location could not be determined from the registry or submitted address."
        testId="hq-empty"
      />
    );
  }
  const confidence = CONFIDENCE_LABEL[get("hq_address_confidence") ?? "low"];
  const source = get("hq_address_source");
  return (
    <div data-testid="hq-panel">
      <StaticMap lat={lat} lon={lon} />
      <a
        href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`}
        target="_blank"
        rel="noreferrer"
        style={styles.osmLink}
        data-testid="hq-osm-link"
      >
        View on OpenStreetMap ↗
      </a>
      <FieldRows
        rows={[
          { label: "Address", value: get("hq_display_name") },
          {
            label: "Geocoded from",
            value: source === "registry" ? "Registry legal address" : "Submitted billing address",
          },
        ]}
      />
      <div
        style={{
          ...styles.flag,
          ...(confidence.tone === "warn" ? styles.flagWarn : styles.flagOk),
        }}
        data-testid="hq-confidence"
      >
        Address confidence: {confidence.text}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DNS & Domain Intelligence (Tier-2)
// ---------------------------------------------------------------------------

interface DomainPanelProps {
  evidence: EvidenceItem[];
  status: string; // section_statuses.evidence
  infrastructureScore: number | null;
}

export function DomainPanel({
  evidence,
  status,
  infrastructureScore,
}: DomainPanelProps) {
  if (status === "pending") return <Pending testId="domain-pending" />;

  const get = (f: string) => evValue(findEvidence(evidence, f, "domain"));
  const recentlyRegistered = findEvidence(evidence, "recently_registered", "domain");
  const noMx = findEvidence(evidence, "no_mx", "domain");

  const ageDays = get("domain_age_days");
  const mx = get("mx_records");
  const sslBefore = get("ssl_not_before");
  const sslAfter = get("ssl_not_after");

  const rows: Row[] = [
    {
      label: "Domain Age",
      value: ageDays !== null ? `${ageDays} days` : null,
      flag: recentlyRegistered
        ? { text: "Recently registered", tone: "warn" }
        : undefined,
    },
    { label: "Registrar", value: get("domain_registrar") },
    { label: "Created", value: get("domain_creation_date") },
    { label: "Expires", value: get("domain_expiry_date") },
    {
      label: "MX Records",
      value: noMx ? "None" : mx,
      flag: noMx ? { text: "No mail records", tone: "warn" } : undefined,
    },
    { label: "SPF", value: get("spf_record") },
    { label: "DKIM", value: get("dkim_present") },
    { label: "SSL Issuer", value: get("ssl_issuer") },
    { label: "SSL Subject", value: get("ssl_subject") },
    {
      label: "SSL Validity",
      value:
        sslBefore || sslAfter ? `${sslBefore ?? "?"} → ${sslAfter ?? "?"}` : null,
    },
    {
      label: "Infrastructure Score",
      value:
        infrastructureScore !== null
          ? `${infrastructureScore.toFixed(0)} / 100`
          : null,
    },
  ];

  const hasData = rows.some((r) => r.value !== null);
  if (!hasData) {
    return (
      <NotAvailable
        label="No domain/DNS evidence available for this report."
        testId="domain-empty"
      />
    );
  }

  return (
    <div data-testid="domain-panel">
      <FieldRows rows={rows} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Registry Information (Tier-1)
// ---------------------------------------------------------------------------

interface RegistryPanelProps {
  evidence: EvidenceItem[];
  status: string;
}

export function RegistryPanel({ evidence, status }: RegistryPanelProps) {
  if (status === "pending") return <Pending testId="registry-pending" />;

  const get = (f: string) => evValue(findEvidence(evidence, f, "opencorporates"));

  const rows: Row[] = [
    { label: "Registered Name", value: get("company_name") },
    { label: "Registration Status", value: get("registration_status") },
    { label: "Jurisdiction", value: get("jurisdiction") },
    { label: "Registration Number", value: get("registration_number") },
    { label: "Legal Address", value: get("legal_address") },
  ];

  const hasData = rows.some((r) => r.value !== null);
  if (!hasData) {
    return (
      <NotAvailable
        label="No authoritative registry record found for this entity."
        testId="registry-empty"
      />
    );
  }

  return (
    <div data-testid="registry-panel">
      <FieldRows rows={rows} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contact Information (Tier-3, with source attribution)
// ---------------------------------------------------------------------------

interface ContactPanelProps {
  evidence: EvidenceItem[];
  status: string;
}

/** Pull the human-readable source attribution (source_url) off an evidence row. */
function attributionOf(item: EvidenceItem | undefined): string | null {
  if (!item) return null;
  const url = item.attribution?.["source_url"];
  if (typeof url === "string") return url;
  return item.source;
}

export function ContactPanel({ evidence, status }: ContactPanelProps) {
  if (status === "pending") return <Pending testId="contact-pending" />;

  const brand = findEvidence(evidence, "web_brand", "web");
  const email = findEvidence(evidence, "web_contacts_email", "web");
  const phone = findEvidence(evidence, "web_contacts_phone", "web");
  const address = findEvidence(evidence, "web_contacts_address", "web");

  const rows: Row[] = [
    {
      label: "Brand / Site Name",
      value: evValue(brand),
      attribution: attributionOf(brand),
    },
    {
      label: "Email",
      value: evValue(email),
      attribution: attributionOf(email),
    },
    {
      label: "Phone",
      value: evValue(phone),
      attribution: attributionOf(phone),
    },
    {
      label: "Address",
      value: evValue(address),
      attribution: attributionOf(address),
    },
  ];

  const hasData = rows.some((r) => r.value !== null);
  if (!hasData) {
    return (
      <NotAvailable
        label="No public contact information was extracted."
        testId="contact-empty"
      />
    );
  }

  return (
    <div data-testid="contact-panel">
      <FieldRows rows={rows.filter((r) => r.value !== null)} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Risk Assessment (scores, triage, flags, evidence summary)
// ---------------------------------------------------------------------------

interface RiskAssessmentPanelProps {
  scores: ScoresData | null;
  status: string; // section_statuses.scores
  sources: SourceSummary[];
}

const LAYER_LABELS: Record<string, string> = {
  entity: "Entity Legitimacy",
  infrastructure: "Infrastructure Legitimacy",
  representation: "Representation Confidence",
  risk: "Fraud / Staging Risk",
};

function ScoreCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div style={styles.scoreCell}>
      <div style={styles.scoreCellValue}>
        {value !== null ? value.toFixed(0) : "—"}
      </div>
      <div style={styles.scoreCellLabel}>{label}</div>
    </div>
  );
}

export function RiskAssessmentPanel({
  scores,
  status,
  sources,
}: RiskAssessmentPanelProps) {
  if (status === "pending") return <Pending testId="risk-pending" />;
  if (!scores) {
    return (
      <NotAvailable
        label="Risk assessment has not been computed yet."
        testId="risk-empty"
      />
    );
  }

  const signals = scores.contributing_signals ?? [];
  const elevated = signals.filter((s) => s.direction === "elevated");
  const trust = signals.filter((s) => s.direction === "trust");

  const available = sources.filter((s) => s.status !== "unavailable");
  const unavailable = sources.filter((s) => s.status === "unavailable");
  const evidenceCount = available.reduce((n, s) => n + s.evidence_count, 0);

  return (
    <div data-testid="risk-panel">
      {/* Layer score breakdown */}
      <div style={styles.scoreGrid}>
        <ScoreCell label="Entity" value={scores.entity_score} />
        <ScoreCell label="Infrastructure" value={scores.infrastructure_score} />
        <ScoreCell label="Representation" value={scores.representation_score} />
        <ScoreCell label="Fraud/Staging" value={scores.risk_score} />
      </div>

      {scores.triage_tier && (
        <div style={styles.triageRow} data-testid="risk-triage">
          Triage tier: <strong>{scores.triage_tier}</strong>
        </div>
      )}

      {/* Evidence summary */}
      <div style={styles.evidenceSummary} data-testid="risk-evidence-summary">
        {available.length} source{available.length === 1 ? "" : "s"} ·{" "}
        {evidenceCount} evidence item{evidenceCount === 1 ? "" : "s"}
      </div>
      {unavailable.length > 0 && (
        <div style={styles.unavailableSources} data-testid="risk-unavailable-sources">
          Unavailable during this run (confidence reduced):{" "}
          {unavailable.map((s) => s.source).join(", ")}
        </div>
      )}

      {/* Risk flags */}
      <div style={styles.flagsSection}>
        <div style={styles.flagsHeading}>Risk Flags</div>
        {elevated.length === 0 ? (
          <div style={styles.noFlags} data-testid="risk-no-flags">
            No elevated-risk signals.
          </div>
        ) : (
          <ul style={styles.flagList} data-testid="risk-flag-list">
            {elevated.map((s) => (
              <li key={s.name} style={styles.flagItem}>
                <span style={{ ...styles.flag, ...styles.flagWarn }}>
                  {LAYER_LABELS[s.layer] ?? s.layer}
                </span>{" "}
                <strong>{s.name}</strong>
                {s.description ? ` — ${s.description}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      {trust.length > 0 && (
        <div style={styles.flagsSection}>
          <div style={styles.flagsHeading}>Trust Signals</div>
          <ul style={styles.flagList} data-testid="risk-trust-list">
            {trust.map((s) => (
              <li key={s.name} style={styles.flagItem}>
                <span style={{ ...styles.flag, ...styles.flagOk }}>
                  {LAYER_LABELS[s.layer] ?? s.layer}
                </span>{" "}
                <strong>{s.name}</strong>
                {s.description ? ` — ${s.description}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles (inline, consistent with the rest of the app)
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  rows: {
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  row: {
    display: "grid",
    gridTemplateColumns: "10rem 1fr",
    gap: "0.75rem",
    alignItems: "start",
    paddingBottom: "0.5rem",
    borderBottom: "1px solid #f3f4f6",
  },
  rowLabel: {
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#6b7280",
  },
  rowValue: {
    fontSize: "0.875rem",
    color: "#111827",
    wordBreak: "break-word",
  },
  attribution: {
    fontSize: "0.7rem",
    color: "#9ca3af",
    marginTop: "0.15rem",
  },
  flag: {
    display: "inline-block",
    marginLeft: "0.4rem",
    padding: "0.05rem 0.4rem",
    borderRadius: "9999px",
    fontSize: "0.7rem",
    fontWeight: 600,
  },
  flagWarn: {
    color: "#92400e",
    backgroundColor: "#fffbeb",
    border: "1px solid #fcd34d",
  },
  flagOk: {
    color: "#166534",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
  },
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
  scoreGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: "0.75rem",
    marginBottom: "1rem",
  },
  scoreCell: {
    textAlign: "center",
    padding: "0.5rem",
    backgroundColor: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
  },
  scoreCellValue: {
    fontSize: "1.5rem",
    fontWeight: 700,
    color: "#111827",
    lineHeight: 1.1,
  },
  scoreCellLabel: {
    fontSize: "0.7rem",
    color: "#6b7280",
    marginTop: "0.2rem",
  },
  triageRow: {
    fontSize: "0.875rem",
    color: "#374151",
    marginBottom: "0.5rem",
    textTransform: "capitalize" as React.CSSProperties["textTransform"],
  },
  map: {
    position: "relative",
    overflow: "hidden",
    width: "100%",
    height: "240px",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
    backgroundColor: "#e5e7eb",
  },
  marker: {
    position: "absolute",
    left: "50%",
    top: "50%",
    width: "14px",
    height: "14px",
    marginLeft: "-7px",
    marginTop: "-7px",
    borderRadius: "50%",
    backgroundColor: "#dc2626",
    border: "3px solid #ffffff",
    boxShadow: "0 0 0 1px rgba(0,0,0,0.35), 0 2px 6px rgba(0,0,0,0.4)",
  },
  mapAttribution: {
    position: "absolute",
    right: 0,
    bottom: 0,
    padding: "1px 6px",
    fontSize: "0.7rem",
    backgroundColor: "rgba(255,255,255,0.85)",
    color: "#374151",
  },
  osmLink: {
    display: "inline-block",
    margin: "0.4rem 0 0.75rem",
    fontSize: "0.8rem",
    color: "#2563eb",
  },
  evidenceSummary: {
    fontSize: "0.8rem",
    color: "#6b7280",
    marginBottom: "1rem",
  },
  unavailableSources: {
    fontSize: "0.8rem",
    color: "#92400e",
    backgroundColor: "#fef3c7",
    padding: "0.4rem 0.6rem",
    borderRadius: "0.375rem",
    marginTop: "-0.5rem",
    marginBottom: "1rem",
  },
  flagsSection: {
    marginTop: "0.75rem",
  },
  flagsHeading: {
    fontSize: "0.85rem",
    fontWeight: 600,
    color: "#111827",
    marginBottom: "0.4rem",
  },
  noFlags: {
    fontSize: "0.85rem",
    color: "#16a34a",
  },
  flagList: {
    margin: 0,
    paddingLeft: "1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.4rem",
  },
  flagItem: {
    fontSize: "0.85rem",
    color: "#374151",
    lineHeight: 1.4,
  },
};
