/**
 * IdentityCorroborationPanel + diff rows (IC1-T6, ticket 0012).
 *
 * Tax-ID and LinkedIn sections each render pending / unavailable /
 * not-found / populated states; the Tax ID diff row and the new comparison
 * rows get readable labels.
 */

import { render, screen, within } from "@testing-library/react";
import { IdentityCorroborationPanel } from "./DetailPanels";
import { RegistrationDiff } from "./RegistrationDiff";
import type { EvidenceItem, MismatchItem, SourceSummary } from "../api/client";

let seq = 0;
function ev(source: string, field: string, value: string, attribution?: Record<string, unknown>): EvidenceItem {
  seq += 1;
  return {
    id: `ev-${seq}`,
    source,
    tier: source === "tax_id" ? 1 : 3,
    field,
    raw_value: value,
    normalized_value: value,
    confidence: 0.9,
    attribution: attribution ?? { provider: "stub" },
    fetched_at: "2026-09-24T12:00:00Z",
  };
}

const PAGE_URL = "https://www.linkedin.com/company/acme-corp";
const li = (field: string, value: string) =>
  ev("linkedin", field, value, { provider: "stub", source_url: PAGE_URL });

const VERIFIED_TAX = [
  ev("tax_id", "tax_id_status", "verified"),
  ev("tax_id", "tax_id_registered_name", "Acme Corporation"),
  ev("tax_id", "tax_id_name_match", "match"),
];

const LINKEDIN_FOUND = [
  li("linkedin_presence", "found"),
  li("linkedin_company_url", PAGE_URL),
  li("linkedin_company_name", "Acme Corporation"),
  li("linkedin_employee_count", "1200"),
  li("linkedin_followers", "45000"),
  li("linkedin_founded_year", "1998"),
  li("linkedin_website", "acme.com"),
  li("linkedin_requester_match", "true"),
];

const WEBSITE_MATCH: MismatchItem = {
  id: "fc-1",
  field_name: "linkedin_website",
  submitted_value: "acme.com",
  discovered_value: "acme.com",
  match_status: "match",
  evidence_id: null,
};

function renderPanel(
  evidence: EvidenceItem[],
  opts: { status?: string; sources?: SourceSummary[]; mismatches?: MismatchItem[] } = {}
) {
  return render(
    <IdentityCorroborationPanel
      evidence={evidence}
      status={opts.status ?? "complete"}
      sources={opts.sources ?? []}
      mismatches={opts.mismatches ?? []}
    />
  );
}

describe("IdentityCorroborationPanel", () => {
  it("shows a pending notice while evidence is still being gathered", () => {
    renderPanel([], { status: "pending" });
    expect(screen.getByTestId("identity-pending")).toBeDefined();
  });

  it("renders a verified tax ID with registered name and a match badge", () => {
    renderPanel(VERIFIED_TAX);
    const tax = screen.getByTestId("identity-tax-id");
    expect(within(tax).getByText("Verified")).toBeDefined();
    expect(within(tax).getByText("Acme Corporation")).toBeDefined();
    expect(within(tax).getByText("Name matches")).toBeDefined();
    expect(within(tax).getByText(/source: stub/)).toBeDefined();
  });

  it("flags a tax ID that is not found or registered to another name", () => {
    renderPanel([ev("tax_id", "tax_id_status", "not_found")]);
    expect(within(screen.getByTestId("identity-tax-id")).getByText("Not found")).toBeDefined();

    renderPanel([
      ev("tax_id", "tax_id_status", "verified"),
      ev("tax_id", "tax_id_registered_name", "Globex Ltd"),
      ev("tax_id", "tax_id_name_match", "mismatch"),
    ]);
    expect(screen.getAllByText("Name differs").length).toBe(1);
  });

  it("explains an unavailable tax-ID source instead of showing blanks", () => {
    renderPanel([], {
      sources: [{ source: "tax_id", tier: 1, evidence_count: 0, attribution: null, status: "unavailable" }],
    });
    expect(screen.getByTestId("identity-tax-id-unavailable").textContent).toMatch(/not available/i);
  });

  it("renders a LinkedIn page with footprint, website match, requester and attribution", () => {
    renderPanel(LINKEDIN_FOUND, { mismatches: [WEBSITE_MATCH] });
    const panel = screen.getByTestId("identity-linkedin");
    const link = within(panel).getByRole("link", { name: PAGE_URL });
    expect(link.getAttribute("href")).toBe(PAGE_URL);
    expect(within(panel).getByText("1200")).toBeDefined();
    expect(within(panel).getByText("45000")).toBeDefined();
    expect(within(panel).getByText("1998")).toBeDefined();
    expect(within(panel).getByText("Website matches")).toBeDefined();
    expect(within(panel).getByText("Requester associated")).toBeDefined();
    expect(within(panel).getAllByText(`source: ${PAGE_URL}`).length).toBeGreaterThan(0);
  });

  it("flags a submitted LinkedIn page that does not exist", () => {
    renderPanel([li("linkedin_presence", "not_found")]);
    expect(screen.getByTestId("identity-linkedin-not-found")).toBeDefined();
  });

  it("explains an unavailable LinkedIn source", () => {
    renderPanel(VERIFIED_TAX, {
      sources: [{ source: "linkedin", tier: 3, evidence_count: 0, attribution: null, status: "unavailable" }],
    });
    expect(screen.getByTestId("identity-linkedin-unavailable")).toBeDefined();
    // The tax-ID section is unaffected.
    expect(screen.getByTestId("identity-tax-id")).toBeDefined();
  });
});

describe("RegistrationDiff identity rows", () => {
  it("labels the tax-ID and LinkedIn comparison rows", () => {
    const rows: MismatchItem[] = [
      { id: "a", field_name: "tax_id", submitted_value: "12-3456789", discovered_value: "verified", match_status: "match", evidence_id: "e1" },
      { id: "b", field_name: "tax_id_registered_name", submitted_value: "Acme", discovered_value: "Globex Ltd", match_status: "mismatch", evidence_id: "e2" },
      { ...WEBSITE_MATCH, id: "c" },
    ];
    render(<RegistrationDiff mismatches={rows} sectionStatus="complete" />);
    expect(screen.getByText("Tax ID")).toBeDefined();
    expect(screen.getByText("Registered Name (tax ID)")).toBeDefined();
    expect(screen.getByText("Website (LinkedIn)")).toBeDefined();
  });
});
