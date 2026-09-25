/** System-disposition badge for individual screening (ticket 0043). */

const BADGE: Record<string, { bg: string; fg: string }> = {
  MATCH: { bg: "#fee2e2", fg: "#991b1b" },
  REVIEW: { bg: "#fef3c7", fg: "#92400e" },
  CLEAR: { bg: "#dcfce7", fg: "#166534" },
};

export function DispositionBadge({ value, auto }: { value: string | null; auto?: boolean | null }) {
  const cfg = BADGE[value ?? ""] ?? { bg: "#e5e7eb", fg: "#374151" };
  return (
    <span
      data-testid="screening-badge"
      style={{ ...badgeStyle, backgroundColor: cfg.bg, color: cfg.fg }}
    >
      {value ?? "PENDING"}
      {value === "CLEAR" && auto ? " (auto)" : ""}
    </span>
  );
}


const badgeStyle: React.CSSProperties = {
  padding: "0.125rem 0.5rem",
  borderRadius: "9999px",
  fontSize: "0.75rem",
  fontWeight: 600,
};
