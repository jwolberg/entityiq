/**
 * CompanyDetail tests:
 *   - renders submitted-vs-discovered with visible mismatch markers
 *   - marking reviewed updates the status
 *   - partial/in-progress report renders without crashing
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { CompanyDetail, POLL_INTERVAL_MS } from "./CompanyDetail";
import { AuthContext } from "../auth/AuthContext";
import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MOCK_AUTH = {
  auth: { token: "test-token", operatorId: "op-1", role: "operator" },
  signOut: vi.fn(),
};

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <AuthContext.Provider value={MOCK_AUTH}>{children}</AuthContext.Provider>
  );
}

const COMPLETE_REPORT = {
  run_id: "run-abc",
  report_id: "rep-abc",
  status: "complete",
  section_statuses: {
    scores: "complete",
    evidence: "complete",
    mismatches: "complete",
    sources: "complete",
  },
  scores: {
    overall_score: 65,
    entity_score: 50,
    infrastructure_score: 70,
    representation_score: 60,
    risk_score: 65,
    triage_tier: "review",
    contributing_signals: [
      {
        name: "recently_registered",
        layer: "infrastructure",
        direction: "elevated",
        weight: 0.3,
        description: "Domain registered within the last 180 days",
        evidence_ids: ["ev-dom-1"],
      },
      {
        name: "registry_confirmed",
        layer: "entity",
        direction: "trust",
        weight: 0.4,
        description: "Entity found in authoritative registry",
        evidence_ids: ["ev-reg-1"],
      },
    ],
  },
  evidence: [
    {
      id: "ev-dom-1",
      source: "domain",
      tier: 2,
      field: "domain_age_days",
      raw_value: "42",
      normalized_value: "42",
      confidence: 0.8,
      attribution: { provider: "domain", domain: "acme.com" },
      fetched_at: "2026-05-26T09:00:00Z",
    },
    {
      id: "ev-dom-flag",
      source: "domain",
      tier: 2,
      field: "recently_registered",
      raw_value: "true",
      normalized_value: "true",
      confidence: 0.9,
      attribution: { provider: "domain", domain: "acme.com" },
      fetched_at: "2026-05-26T09:00:00Z",
    },
    {
      id: "ev-dom-2",
      source: "domain",
      tier: 2,
      field: "domain_registrar",
      raw_value: "NameCheap, Inc.",
      normalized_value: "NameCheap, Inc.",
      confidence: 0.75,
      attribution: { provider: "domain", domain: "acme.com" },
      fetched_at: "2026-05-26T09:00:00Z",
    },
    {
      id: "ev-reg-1",
      source: "opencorporates",
      tier: 1,
      field: "registration_status",
      raw_value: "Active",
      normalized_value: "Active",
      confidence: 0.9,
      attribution: { provider: "opencorporates" },
      fetched_at: "2026-05-26T09:00:00Z",
    },
    {
      id: "ev-web-1",
      source: "web",
      tier: 3,
      field: "web_contacts_email",
      raw_value: "hello@acme.com",
      normalized_value: "hello@acme.com",
      confidence: 0.7,
      attribution: { provider: "web", source_url: "https://acme.com/contact" },
      fetched_at: "2026-05-26T09:00:00Z",
    },
  ],
  mismatches: [
    {
      id: "fc-1",
      field_name: "company_name",
      submitted_value: "Acme Corp",
      discovered_value: "ACME CORPORATION",
      match_status: "mismatch",
      evidence_id: "ev-1",
    },
    {
      id: "fc-2",
      field_name: "domain",
      submitted_value: "acme.com",
      discovered_value: "acme.com",
      match_status: "match",
      evidence_id: "ev-2",
    },
    {
      id: "fc-3",
      field_name: "country",
      submitted_value: "US",
      discovered_value: null,
      match_status: "unverified",
      evidence_id: null,
    },
  ],
  sources: [
    { source: "domain", tier: 2, evidence_count: 2, attribution: null },
    { source: "opencorporates", tier: 1, evidence_count: 1, attribution: null },
    { source: "web", tier: 3, evidence_count: 1, attribution: null },
  ],
  generated_at: "2026-05-26T10:00:00Z",
};

const PARTIAL_REPORT = {
  run_id: "run-partial",
  report_id: "rep-partial",
  status: "partial",
  section_statuses: {
    scores: "pending",
    evidence: "pending",
    mismatches: "pending",
    sources: "pending",
  },
  scores: null,
  evidence: [],
  mismatches: [],
  sources: [],
  generated_at: null,
};

/** Serves report bodies in order from /reports/*, empty audit elsewhere. */
function routedFetch(...reports: unknown[]) {
  const queue = [...reports];
  const reportCalls: string[] = [];
  const mock = vi.fn(async (url: string) => {
    if (url.startsWith("/api/reports/")) {
      reportCalls.push(url);
      const body = queue.length > 1 ? queue.shift() : queue[0];
      return { ok: true, json: async () => body };
    }
    return { ok: true, json: async () => ({ events: [] }) };
  });
  return { mock, reportCalls };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CompanyDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("treats an unknown address-confidence value as low instead of crashing", async () => {
    const geo = (field: string, value: string) => ({
      id: `ev-${field}`,
      source: "geocode",
      tier: 3,
      field,
      raw_value: value,
      normalized_value: value,
      confidence: 0.7,
      attribution: null,
      fetched_at: null,
    });
    const report = {
      ...COMPLETE_REPORT,
      evidence: [
        ...COMPLETE_REPORT.evidence,
        geo("hq_latitude", "47.6"),
        geo("hq_longitude", "-122.3"),
        geo("hq_address_confidence", "unexpected-value"),
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => report })
    );
    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByTestId("hq-confidence")).toBeDefined());
    expect(screen.getByTestId("hq-confidence").textContent).toContain("Low");
  });

  it("colors the headline score by triage tier and names the tier", async () => {
    const report = {
      ...COMPLETE_REPORT,
      scores: { ...COMPLETE_REPORT.scores, overall_score: 22, triage_tier: "escalate" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => report })
    );
    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByTestId("detail-overall-score")).toBeDefined());
    expect(
      screen.getByTestId("detail-overall-score").getAttribute("style")
    ).toContain("rgb(220, 38, 38)");
    expect(screen.getByTestId("detail-triage-tier").textContent).toContain("Escalate");
  });

  it("renders the HQ map at the geocoded point with address confidence", async () => {
    const geo = (field: string, value: string) => ({
      id: `ev-geo-${field}`,
      source: "geocode",
      tier: 3,
      field,
      raw_value: value,
      normalized_value: value,
      confidence: 0.7,
      attribution: { provider: "openstreetmap_nominatim" },
      fetched_at: null,
    });
    const report = {
      ...COMPLETE_REPORT,
      evidence: [
        ...COMPLETE_REPORT.evidence,
        geo("hq_latitude", "47.6114"),
        geo("hq_longitude", "-122.3366"),
        geo("hq_display_name", "400 Pine Street, Seattle"),
        geo("hq_address_source", "registry"),
        geo("hq_address_confidence", "high"),
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => report })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("hq-panel")).toBeDefined();
    });
    // Static tile map: the centre tile for (47.6114, -122.3366) at z15 is 5248/11443.
    const tiles = [...screen.getByTestId("hq-map").querySelectorAll("img")].map(
      (img) => img.getAttribute("src")
    );
    expect(tiles).toContain("https://tile.openstreetmap.org/15/5248/11443.png");
    expect(tiles).toHaveLength(21); // 7 × 3 grid
    expect(screen.getByTestId("hq-marker")).toBeDefined();
    expect(screen.getByTestId("hq-osm-link").getAttribute("href")).toContain(
      "mlat=47.6114&mlon=-122.3366"
    );
    expect(screen.getByTestId("hq-confidence").textContent).toContain("High");
    expect(screen.getByText("400 Pine Street, Seattle")).toBeDefined();
  });

  it("explains a missing HQ location instead of showing an empty map", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => COMPLETE_REPORT })
    );
    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );
    await waitFor(() => {
      expect(screen.getByTestId("hq-empty")).toBeDefined();
    });
    expect(screen.queryByTestId("hq-map")).toBeNull();
  });

  it("shows a saved review on reload instead of offering Mark Reviewed again", async () => {
    const report = {
      ...COMPLETE_REPORT,
      review: {
        status: "reviewed",
        notes: "Registry confirmed by phone.",
        reviewer_name: "Rita Reviewer",
        decided_at: "2026-09-20T15:00:00Z",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => report })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("reviewed-banner")).toBeDefined();
    });
    const banner = screen.getByTestId("reviewed-banner").textContent ?? "";
    expect(banner).toContain("reviewed");
    expect(banner).toContain("Rita Reviewer");
    expect(screen.getByTestId("review-notes-display").textContent).toContain(
      "Registry confirmed by phone."
    );
    expect(screen.queryByTestId("mark-reviewed-btn")).toBeNull();
  });

  it("lists unavailable sources instead of silently counting fewer", async () => {
    const report = {
      ...COMPLETE_REPORT,
      sources: [
        { source: "domain", tier: 2, evidence_count: 2, attribution: null, status: "available" },
        { source: "opencorporates", tier: 1, evidence_count: 0, attribution: null, status: "unavailable" },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => report })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("risk-panel")).toBeDefined();
    });
    expect(screen.getByTestId("risk-evidence-summary").textContent).toContain(
      "1 source"
    );
    expect(screen.getByTestId("risk-unavailable-sources").textContent).toContain(
      "opencorporates"
    );
  });

  it("renders submitted-vs-discovered fields with mismatch markers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => COMPLETE_REPORT,
      })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("registration-diff")).toBeDefined();
    });

    // Mismatch row for company_name should be visible
    const mismatchRow = screen.getByTestId("diff-row-company_name");
    expect(mismatchRow).toBeDefined();
    expect(mismatchRow.getAttribute("data-status")).toBe("mismatch");
    expect(screen.getByText("Acme Corp")).toBeDefined();
    expect(screen.getByText("ACME CORPORATION")).toBeDefined();

    // Match row for domain
    const matchRow = screen.getByTestId("diff-row-domain");
    expect(matchRow.getAttribute("data-status")).toBe("match");

    // Unverified row for country
    const unverifiedRow = screen.getByTestId("diff-row-country");
    expect(unverifiedRow.getAttribute("data-status")).toBe("unverified");

    // Overall score rendered
    expect(screen.getByTestId("detail-overall-score").textContent).toBe("65");
  });

  it("renders the four detail panels from report evidence and scores", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => COMPLETE_REPORT,
      })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("domain-panel")).toBeDefined();
    });

    // Domain panel: registrar + recently-registered flag
    expect(screen.getByText("NameCheap, Inc.")).toBeDefined();
    expect(screen.getByText("Recently registered")).toBeDefined();

    // Registry panel: registration status
    expect(screen.getByTestId("registry-panel")).toBeDefined();
    expect(screen.getByText("Active")).toBeDefined();

    // Contact panel: email + source attribution
    expect(screen.getByTestId("contact-panel")).toBeDefined();
    expect(screen.getByText("hello@acme.com")).toBeDefined();
    expect(
      screen.getByText("source: https://acme.com/contact")
    ).toBeDefined();

    // Risk panel: flags + trust signals + evidence summary
    expect(screen.getByTestId("risk-flag-list")).toBeDefined();
    expect(screen.getByTestId("risk-trust-list")).toBeDefined();
    expect(screen.getByTestId("risk-evidence-summary").textContent).toContain(
      "3 sources"
    );
    expect(screen.getByTestId("risk-evidence-summary").textContent).toContain(
      "4 evidence items"
    );
  });

  it("detail panels show pending/empty notices for a partial report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => PARTIAL_REPORT,
      })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-partial" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("domain-pending")).toBeDefined();
    });
    expect(screen.getByTestId("registry-pending")).toBeDefined();
    expect(screen.getByTestId("contact-pending")).toBeDefined();
    expect(screen.getByTestId("risk-pending")).toBeDefined();
  });

  it("partial/in-progress report renders without crashing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => PARTIAL_REPORT,
      })
    );

    render(
      <Wrapper>
        <CompanyDetail runId="run-partial" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      // Score section shows pending
      expect(screen.getByTestId("score-pending")).toBeDefined();
    });

    // Diff shows pending notice
    expect(screen.getByTestId("diff-pending")).toBeDefined();
    // No crash
  });

  // Ticket 0001 (plan U21): partial results land without a manual reload.
  it("re-fetches a partial report until the run completes, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const running = {
        ...PARTIAL_REPORT,
        run: {
          status: "running",
          started_at: "2026-09-24T12:00:00+00:00",
          finished_at: null,
          duration_seconds: null,
          stages: { normalize_input: "complete", query_registries: "pending" },
        },
      };
      const done = {
        ...COMPLETE_REPORT,
        run_id: "run-partial",
        run: {
          status: "complete",
          started_at: "2026-09-24T12:00:00+00:00",
          finished_at: "2026-09-24T12:01:30+00:00",
          duration_seconds: 90,
          stages: { normalize_input: "complete", query_registries: "complete" },
        },
      };
      const { mock, reportCalls } = routedFetch(running, done);
      vi.stubGlobal("fetch", mock);

      render(
        <Wrapper>
          <CompanyDetail runId="run-partial" onBack={vi.fn()} />
        </Wrapper>
      );

      await waitFor(() => expect(screen.getByTestId("score-pending")).toBeDefined());
      expect(screen.getByTestId("detail-run-progress").textContent).toContain(
        "1 of 2"
      );

      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
      await waitFor(() =>
        expect(screen.getByTestId("detail-overall-score")).toBeDefined()
      );
      expect(screen.getByTestId("detail-run-progress").textContent).toContain(
        "1m 30s"
      );

      // Complete: no further polling.
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
      expect(reportCalls).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not poll a report that is already complete", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { mock, reportCalls } = routedFetch(COMPLETE_REPORT);
      vi.stubGlobal("fetch", mock);

      render(
        <Wrapper>
          <CompanyDetail runId="run-abc" onBack={vi.fn()} />
        </Wrapper>
      );
      await waitFor(() =>
        expect(screen.getByTestId("detail-overall-score")).toBeDefined()
      );
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
      expect(reportCalls).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("mark-reviewed updates status on success", async () => {
    // Routed by URL/method rather than call order: besides the report and
    // the review POST, the Activity and Ownership panels each fire their
    // own GET on mount (order between them is not guaranteed).
    const mockFetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/reports/")) {
        return { ok: true, json: async () => COMPLETE_REPORT };
      }
      if (init?.method === "POST" && url.startsWith("/api/reviews/")) {
        return {
          ok: true,
          json: async () => ({
            review_id: "rev-1",
            run_id: "run-abc",
            operator_id: "op-1",
            review_status: "reviewed",
            decided_at: "2026-05-26T11:00:00Z",
            message: "Run marked as 'reviewed'.",
          }),
        };
      }
      // Activity panel (GET /audit/runs/*) + Ownership panel (GET
      // /ownership/runs/*/challenges): an empty list either way.
      return { ok: true, json: async () => ({ events: [] }) };
    });

    vi.stubGlobal("fetch", mockFetch);

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    // Wait for report to load
    await waitFor(() => {
      expect(screen.getByTestId("mark-reviewed-btn")).toBeDefined();
    });

    // Click mark reviewed
    fireEvent.click(screen.getByTestId("mark-reviewed-btn"));

    // After review, banner replaces button
    await waitFor(() => {
      expect(screen.getByTestId("reviewed-banner")).toBeDefined();
    });

    const banner = screen.getByTestId("reviewed-banner");
    expect(banner.textContent).toContain("reviewed");
  });

  it("shows review error when mark-reviewed fails", async () => {
    const mockFetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/reports/")) {
        return { ok: true, json: async () => COMPLETE_REPORT };
      }
      if (init?.method === "POST" && url.startsWith("/api/reviews/")) {
        return {
          ok: false,
          json: async () => ({ detail: "Conflict: already reviewed" }),
        };
      }
      // Activity panel (GET /audit/runs/*) + Ownership panel (GET
      // /ownership/runs/*/challenges): an empty list either way.
      return { ok: true, json: async () => ({ events: [] }) };
    });

    vi.stubGlobal("fetch", mockFetch);

    render(
      <Wrapper>
        <CompanyDetail runId="run-abc" onBack={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("mark-reviewed-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("mark-reviewed-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("review-error")).toBeDefined();
    });
  });
});
