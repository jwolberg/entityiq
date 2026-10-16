/**
 * PublicRecordsPanel (ticket 0095): CourtListener dockets + SAM exclusions.
 *
 * Evidence shapes mirror app/adapters/public_records.py. Every name is
 * fictional.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ContributingSignal, EvidenceItem } from "../api/client";
import { PublicRecordsPanel } from "./PublicRecordsPanel";

let seq = 0;
function ev(field: string, raw: string, normalized: string | null, payload: Record<string, unknown>, url?: string): EvidenceItem {
  seq += 1;
  return {
    id: `pr-${seq}`,
    source: "public_records",
    tier: 1,
    field,
    raw_value: raw,
    normalized_value: normalized,
    confidence: field === "public_record" ? 0.9 : null,
    attribution: url ? { provider: "courtlistener", source_url: url } : { provider: "courtlistener" },
    fetched_at: "2026-10-16T12:00:00Z",
    raw_payload: payload,
  };
}

const status = (source: string, value: string, extra: Record<string, unknown> = {}) =>
  ev("public_records_status", value, value, { source, ...extra });

const summary = (payload: Record<string, unknown>) => ev("public_records_summary", "summary", null, payload);

const bankruptcy = (overrides: Record<string, unknown> = {}) => {
  const payload = {
    kind: "bankruptcy",
    match: "strong",
    status: "open",
    chapter: "11",
    court: "United States Bankruptcy Court, D. Delaware",
    docket_number: "25-10417",
    filed_date: "2025-08-12",
    party_name: "Contoso Freight LLC",
    party_role: "debtor",
    freshness: "fresh",
    source_url: "https://www.courtlistener.com/docket/900001/",
    ...overrides,
  };
  return ev("public_record", "bankruptcy", String(payload.match), payload, String(payload.source_url));
};

const exclusion = (overrides: Record<string, unknown> = {}) => {
  const payload = {
    kind: "federal_exclusion",
    match: "strong",
    status: "active",
    classification: "Firm",
    party_name: "Contoso Freight LLC",
    agency: "GSA",
    exclusion_type: "Ineligible (Proceedings Completed)",
    active_date: "2025-03-14",
    termination_date: "Indefinite",
    sam_number: "00000000-0000-4000-8000-000000000001",
    source_url: null,
    ...overrides,
  };
  return ev("public_record", "federal_exclusion", String(payload.match), payload);
};

const civil = (n: number) =>
  Array.from({ length: n }, (_, i) =>
    ev("public_record", "federal_civil", "unscored", {
      kind: "federal_civil",
      match: "strong",
      status: "open",
      court: "United States District Court, D. Delaware",
      docket_number: `1:26-cv-0${1000 + i}`,
      filed_date: "2026-03-15",
      party_name: "Wingtip Supply Co. v. Contoso Freight LLC",
      party_role: "unknown",
      scored: false,
      source_url: `https://www.courtlistener.com/docket/${910000 + i}/`,
    }, `https://www.courtlistener.com/docket/${910000 + i}/`)
  );

function renderPanel(evidence: EvidenceItem[], signals: ContributingSignal[] = [], state = "complete") {
  return render(<PublicRecordsPanel evidence={evidence} status={state} signals={signals} />);
}

describe("PublicRecordsPanel", () => {
  it("shows a pending notice while evidence is gathered", () => {
    renderPanel([], [], "pending");
    expect(screen.getByTestId("public-records-pending")).toBeTruthy();
  });

  it("says the stage did not run when there is no public-records evidence", () => {
    renderPanel([]);
    expect(screen.getByTestId("public-records-not-run")).toBeTruthy();
  });

  it.each([
    ["searched", /searched/i],
    ["no_records", /no records found/i],
    ["unavailable", /not available/i],
    ["timeout", /could not be checked \(temporary\)/i],
    ["rate_limited", /could not be checked \(temporary\)/i],
    ["not_applicable", /not applicable/i],
  ])("renders the %s status for each source", (value, text) => {
    renderPanel([status("courtlistener", value), status("sam_exclusions", value)]);
    const rows = screen.getAllByTestId(/public-records-status-/);
    expect(rows).toHaveLength(2);
    rows.forEach((row) => expect(row.textContent).toMatch(text));
  });

  it("marks a stale SAM snapshot with its date", () => {
    renderPanel([status("sam_exclusions", "stale", { snapshot_retrieved_at: "2026-10-01T00:00:00+00:00" })]);
    const row = screen.getByTestId("public-records-status-sam_exclusions");
    expect(row.textContent).toMatch(/stale/i);
    expect(row.textContent).toMatch("2026-10-01");
  });

  it("states what no records does and does not cover, without success styling", () => {
    renderPanel([status("courtlistener", "no_records"), status("sam_exclusions", "no_records")]);
    const coverage = screen.getByTestId("public-records-coverage");
    expect(coverage.textContent).toMatch(/state courts, liens and UCC filings are not covered/i);
    expect(screen.queryByText(/clear|passed|✓/i)).toBeNull();
  });

  it("lists strong records by kind, exclusions first, citing docket and agency", () => {
    renderPanel([status("courtlistener", "searched"), bankruptcy(), exclusion()]);
    const groups = screen.getAllByTestId(/public-records-group-/).map((g) => g.dataset.testid);
    expect(groups).toEqual(["public-records-group-federal_exclusion", "public-records-group-bankruptcy"]);
    const bk = screen.getByTestId("public-records-group-bankruptcy");
    expect(bk.textContent).toMatch("25-10417");
    expect(bk.textContent).toMatch("D. Delaware");
    expect(bk.textContent).toMatch("2025-08-12");
    expect(bk.textContent).toMatch(/chapter 11/i);
    const ex = screen.getByTestId("public-records-group-federal_exclusion");
    expect(ex.textContent).toMatch("GSA");
    expect(ex.textContent).toMatch("2025-03-14");
    expect(ex.textContent).toMatch("00000000-0000-4000-8000-000000000001");
  });

  it("links dockets on CourtListener with a descriptive name", () => {
    renderPanel([bankruptcy()]);
    const link = screen.getByRole("link", { name: /view docket 25-10417 on courtlistener/i });
    expect(link.getAttribute("href")).toBe("https://www.courtlistener.com/docket/900001/");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("never links a URL outside the https allowlist", () => {
    renderPanel([
      bankruptcy({ docket_number: "BAD-1", source_url: "javascript:alert(1)" }),
      bankruptcy({ docket_number: "BAD-2", source_url: "http://www.courtlistener.com/docket/1/" }),
      bankruptcy({ docket_number: "BAD-3", source_url: "https://evil.example/docket/1/" }),
    ]);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText(/BAD-1/)).toBeTruthy();
  });

  it("puts weak matches under Possible matches and marks them not scored", () => {
    renderPanel([exclusion({ match: "weak", classification: "Individual", party_name: "Jordan Example" })]);
    const possible = screen.getByTestId("public-records-possible");
    expect(within(possible).getByText(/Jordan Example/)).toBeTruthy();
    expect(possible.textContent).toMatch(/not scored/i);
    expect(screen.queryByTestId("public-records-group-federal_exclusion")).toBeNull();
  });

  it("shows civil cases as not scored with an unknown role", () => {
    renderPanel([status("courtlistener", "searched"), ...civil(2)]);
    const group = screen.getByTestId("public-records-group-federal_civil");
    expect(group.textContent).toMatch(/not scored/i);
    expect(group.textContent).toMatch(/role unknown/i);
  });

  it("marks records that drive a risk signal", () => {
    const bk = bankruptcy();
    renderPanel([bk], [
      { name: "public_records_open_bankruptcy", layer: "entity", direction: "elevated", weight: 0.8, description: "", evidence_ids: [bk.id] },
    ]);
    expect(screen.getByTestId("public-records-group-bankruptcy").textContent).toMatch(/counts toward risk/i);
  });

  it("shows the true total when a search was truncated", () => {
    renderPanel([
      status("courtlistener", "searched", { truncated: true }),
      summary({ federal_civil: { total: 57, stored: 5 } }),
      ...civil(5),
    ]);
    expect(screen.getByTestId("public-records-group-federal_civil").textContent).toMatch(/showing 5 of 57/i);
  });

  it("renders a failed CourtListener next to a SAM hit", () => {
    renderPanel([status("courtlistener", "rate_limited"), status("sam_exclusions", "searched"), exclusion()]);
    expect(screen.getByTestId("public-records-status-courtlistener").textContent).toMatch(/temporary/i);
    expect(screen.getByTestId("public-records-group-federal_exclusion")).toBeTruthy();
  });

  it("shows a redacted party name as given", () => {
    renderPanel([exclusion({ match: "weak", party_name: "[redacted]" })]);
    expect(screen.getByText(/\[redacted\]/)).toBeTruthy();
  });
});
