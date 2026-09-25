/**
 * Plain-text rendering of watchlist values (DOBs, documents, name lists) for
 * people reading evidence, instead of raw JSON (ticket 0070 UX review).
 */

type Obj = Record<string, unknown>;

function formatOne(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "object") return String(v);
  const o = v as Obj;
  if (typeof o.date === "string") return o.date;
  if (o.from_year != null && o.to_year != null) return `${o.from_year}–${o.to_year}`;
  if (o.year != null) {
    const base = o.month != null ? `${o.year}-${String(o.month).padStart(2, "0")}` : String(o.year);
    return o.circa ? `about ${base}` : base;
  }
  if (typeof o.number === "string") {
    const where = o.country ? ` (${o.country})` : "";
    return `${o.type ?? "document"} ${o.number}${where}`;
  }
  if (typeof o.name === "string") return o.name;
  return Object.entries(o)
    .map(([k, val]) => `${k}: ${formatOne(val)}`)
    .join(", ");
}

export function formatListValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(formatOne).join("; ") : "—";
  return formatOne(value);
}
