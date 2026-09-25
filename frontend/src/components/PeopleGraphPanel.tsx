/**
 * PeopleGraphPanel — the officers and owners behind a company (ticket 0084).
 *
 * Dependency-free inline SVG: the company in the center, each person on a
 * ring around it, the relationship/role/share on the edge. A person's
 * screening outcome is a status, so it never rides on color alone: every
 * node carries a glyph and a text label, and a legend names them. Nodes are
 * focusable buttons (click / Enter opens the screening) with a hover/focus
 * tooltip, and a table view shows the same people for screen readers and
 * print.
 *
 * Data: GET /reports/{run_id}/people (ticket 0083). Names come from the
 * encrypted screening subjects; a shredded person is drawn without one.
 */

import { useEffect, useState } from "react";
import { apiClient, RunPerson, RunPeople } from "../api/client";

interface PeopleGraphPanelProps {
  runId: string;
  token: string;
  companyName: string;
  onOpenScreening?: (screeningRunId: string) => void;
}

// Same tokens as DispositionBadge: tint fill, dark ring/ink.
const STATUS: Record<string, { fill: string; ring: string; glyph: string; label: string }> = {
  MATCH: { fill: "#fee2e2", ring: "#991b1b", glyph: "!", label: "MATCH" },
  REVIEW: { fill: "#fef3c7", ring: "#92400e", glyph: "?", label: "REVIEW" },
  CLEAR: { fill: "#dcfce7", ring: "#166534", glyph: "✓", label: "CLEAR" },
};
const NOT_SCREENED = { fill: "#f3f4f6", ring: "#4b5563", glyph: "–", label: "Not screened" };

function status(p: RunPerson) {
  return STATUS[p.disposition ?? ""] ?? NOT_SCREENED;
}

function displayName(p: RunPerson): string {
  if (p.shredded) return "(shredded)";
  return p.name ?? "(unknown)";
}

function edgeLabel(p: RunPerson): string {
  const parts = [p.relationships.join(", ")];
  if (p.roles.length) parts.push(p.roles.join(", "));
  if (p.ownership_pct != null) parts.push(`${p.ownership_pct}%`);
  return parts.join(" · ");
}

function sourceLabel(p: RunPerson): string {
  return p.sources
    .map((s) => (s.provider ? `${s.source} (${s.provider})` : s.source))
    .join(", ");
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

const WIDTH = 640;
const NODE_R = 24;

function layout(count: number) {
  const radius = count <= 6 ? 150 : Math.min(150 + (count - 6) * 14, 260);
  const height = 2 * radius + 130;
  const cx = WIDTH / 2;
  const cy = height / 2;
  const points = Array.from({ length: count }, (_, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / count;
    return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  });
  return { height, cx, cy, points };
}

export function PeopleGraphPanel({
  runId,
  token,
  companyName,
  onOpenScreening,
}: PeopleGraphPanelProps) {
  const [data, setData] = useState<RunPeople | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "table">("graph");
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .getRunPeople(runId, token)
      // Defensive: a malformed/unrelated response renders as empty, never crashes.
      .then(
        (d) =>
          !cancelled &&
          setData({
            run_id: runId,
            sources: d?.sources ?? { registry: null, screening: null },
            people: Array.isArray(d?.people) ? d.people : [],
          })
      )
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [runId, token]);

  if (error) {
    return (
      <p role="alert" style={styles.error}>
        {error}
      </p>
    );
  }
  if (data === null) return <p style={styles.muted}>Loading officers and owners…</p>;

  const gaps: string[] = [];
  if (data.sources.registry === "unavailable") {
    gaps.push(
      "Registry officers and owners weren't checked (the registry source is unavailable); only declared people are shown."
    );
  }
  if (data.sources.screening === "unavailable") {
    gaps.push("People were not screened: individual screening is unavailable for this run.");
  }
  const gapNote =
    gaps.length > 0 ? (
      <p data-testid="people-gap" style={styles.gap}>
        {gaps.join(" ")}
      </p>
    ) : null;

  if (data.people.length === 0) {
    return (
      <div>
        <p data-testid="people-empty" style={styles.muted}>
          No officers or owners were found for this run.
        </p>
        {gapNote}
      </div>
    );
  }

  const people = data.people;
  const open = (p: RunPerson) => p.screening_run_id && onOpenScreening?.(p.screening_run_id);

  return (
    <div>
      <div style={styles.toolbar}>
        <div data-testid="people-legend" style={styles.legend}>
          {[STATUS.MATCH, STATUS.REVIEW, STATUS.CLEAR, NOT_SCREENED].map((s) => (
            <span key={s.label} style={styles.legendItem}>
              <span
                aria-hidden="true"
                style={{ ...styles.legendDot, background: s.fill, borderColor: s.ring, color: s.ring }}
              >
                {s.glyph}
              </span>
              {s.label}
            </span>
          ))}
        </div>
        <div role="group" aria-label="View" style={styles.toggle}>
          {(["graph", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              style={view === v ? styles.toggleOn : styles.toggleOff}
            >
              {v === "graph" ? "Graph" : "Table"}
            </button>
          ))}
        </div>
      </div>

      {view === "graph" ? (
        <Graph
          people={people}
          companyName={companyName}
          active={active}
          setActive={setActive}
          open={open}
        />
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              {["Person", "Relationship", "Share", "Screening", "Human", "Sources", ""].map((h) => (
                <th key={h} style={styles.th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {people.map((p, i) => (
              <tr key={p.screening_run_id ?? i} data-testid="people-row">
                <td style={styles.td}>{displayName(p)}</td>
                <td style={styles.td}>{[...p.relationships, ...p.roles].join(", ")}</td>
                <td style={styles.td}>{p.ownership_pct != null ? `${p.ownership_pct}%` : "—"}</td>
                <td style={styles.td}>
                  {status(p).glyph} {status(p).label}
                </td>
                <td style={styles.td}>{p.human_disposition ?? "—"}</td>
                <td style={styles.td}>{sourceLabel(p)}</td>
                <td style={styles.td}>
                  {p.screening_run_id && onOpenScreening && (
                    <button type="button" onClick={() => open(p)} style={styles.linkBtn}>
                      Open screening
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {gapNote}
    </div>
  );
}

function Graph({
  people,
  companyName,
  active,
  setActive,
  open,
}: {
  people: RunPerson[];
  companyName: string;
  active: number | null;
  setActive: (i: number | null) => void;
  open: (p: RunPerson) => void;
}) {
  const { height, cx, cy, points } = layout(people.length);
  const tip = active !== null ? people[active] : null;
  const tipAt = active !== null ? points[active] : null;

  return (
    <div style={styles.graphWrap}>
      <svg
        data-testid="people-graph"
        viewBox={`0 0 ${WIDTH} ${height}`}
        width="100%"
        role="img"
        aria-label={`${companyName}: ${people.length} officers and owners`}
        style={styles.svg}
      >
        {people.map((p, i) => {
          const { x, y } = points[i];
          // Past the midpoint, clear of the company box; node text sits on
          // the node's outer side, so the two never meet.
          const mx = cx + (x - cx) * 0.62;
          const my = cy + (y - cy) * 0.62;
          return (
            <g key={`edge-${i}`}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke="#94a3b8" strokeWidth={2} />
              <text
                data-testid="person-edge"
                x={mx}
                y={my}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={11}
                fill="#4b5563"
                stroke="#ffffff"
                strokeWidth={4}
                paintOrder="stroke"
              >
                {edgeLabel(p)}
              </text>
            </g>
          );
        })}

        <g>
          <rect x={cx - 90} y={cy - 22} width={180} height={44} rx={8} fill="#1e3a5f" />
          <text
            x={cx}
            y={cy}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={13}
            fontWeight={700}
            fill="#ffffff"
          >
            {truncate(companyName, 24)}
          </text>
        </g>

        {people.map((p, i) => {
          const { x, y } = points[i];
          const s = status(p);
          const name = displayName(p);
          // Labels on the side away from the company, so the edge never
          // runs through them.
          const above = y < cy - 1;
          const nameY = above ? y - NODE_R - 22 : y + NODE_R + 14;
          const statusY = above ? y - NODE_R - 8 : y + NODE_R + 28;
          return (
            <g
              key={`node-${i}`}
              data-testid="person-node"
              role="button"
              tabIndex={0}
              aria-label={`${name}, ${s.label}. Open screening.`}
              onClick={() => open(p)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  open(p);
                }
              }}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              style={{ cursor: "pointer", outline: "none" }}
            >
              {/* Hit target bigger than the mark. */}
              <circle cx={x} cy={y} r={NODE_R + 14} fill="transparent" />
              <circle
                cx={x}
                cy={y}
                r={NODE_R}
                fill={s.fill}
                stroke={s.ring}
                strokeWidth={active === i ? 4 : 2}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={16}
                fontWeight={700}
                fill={s.ring}
                aria-hidden="true"
              >
                {s.glyph}
              </text>
              <text
                x={x}
                y={nameY}
                textAnchor="middle"
                fontSize={12}
                fontWeight={600}
                fill="#111827"
                stroke="#ffffff"
                strokeWidth={4}
                paintOrder="stroke"
              >
                {truncate(name, 22)}
              </text>
              <text
                x={x}
                y={statusY}
                textAnchor="middle"
                fontSize={11}
                fill="#4b5563"
                stroke="#ffffff"
                strokeWidth={4}
                paintOrder="stroke"
              >
                {s.label}
              </text>
            </g>
          );
        })}
      </svg>

      {tip && tipAt && (
        <div
          data-testid="person-tooltip"
          role="tooltip"
          style={{
            ...styles.tooltip,
            left: `${(tipAt.x / WIDTH) * 100}%`,
            top: `${((tipAt.y + NODE_R + 36) / height) * 100}%`,
          }}
        >
          <strong>{displayName(tip)}</strong>
          <div>{[...tip.relationships, ...tip.roles].join(", ")}</div>
          {tip.ownership_pct != null && <div>Ownership: {tip.ownership_pct}%</div>}
          <div>
            Screening: {status(tip).label}
            {tip.top_score != null && ` (top score ${tip.top_score.toFixed(2)})`}
          </div>
          <div>Human decision: {tip.human_disposition ?? "none yet"}</div>
          <div>Sources: {sourceLabel(tip)}</div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  muted: { color: "#6b7280", fontSize: "0.875rem", margin: 0 },
  error: { color: "#dc2626", fontSize: "0.875rem", margin: 0 },
  gap: {
    marginTop: "0.75rem",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
    color: "#4b5563",
    fontSize: "0.8125rem",
  },
  toolbar: {
    display: "flex",
    flexWrap: "wrap",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "0.5rem",
    marginBottom: "0.5rem",
  },
  legend: { display: "flex", flexWrap: "wrap", gap: "0.75rem", fontSize: "0.75rem", color: "#374151" },
  legendItem: { display: "inline-flex", alignItems: "center", gap: "0.3rem" },
  legendDot: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "1.1rem",
    height: "1.1rem",
    borderRadius: "9999px",
    border: "2px solid",
    fontSize: "0.65rem",
    fontWeight: 700,
  },
  toggle: { display: "inline-flex", border: "1px solid #d1d5db", borderRadius: "0.375rem", overflow: "hidden" },
  toggleOn: {
    padding: "0.25rem 0.75rem",
    border: "none",
    backgroundColor: "#1e3a5f",
    color: "#ffffff",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  toggleOff: {
    padding: "0.25rem 0.75rem",
    border: "none",
    backgroundColor: "#ffffff",
    color: "#374151",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  graphWrap: { position: "relative" },
  svg: { display: "block", maxWidth: 640, margin: "0 auto", overflow: "visible" },
  tooltip: {
    position: "absolute",
    transform: "translateX(-50%)",
    zIndex: 1,
    minWidth: "12rem",
    maxWidth: "18rem",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#ffffff",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
    color: "#111827",
    fontSize: "0.75rem",
    lineHeight: 1.5,
    pointerEvents: "none",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" },
  th: {
    textAlign: "left",
    padding: "0.375rem 0.5rem",
    borderBottom: "1px solid #e5e7eb",
    color: "#6b7280",
    fontSize: "0.75rem",
  },
  td: { padding: "0.375rem 0.5rem", borderBottom: "1px solid #f3f4f6", color: "#111827" },
  linkBtn: {
    background: "none",
    border: "none",
    color: "#1d4ed8",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: "0.8125rem",
    padding: 0,
  },
};
