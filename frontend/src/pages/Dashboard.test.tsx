/**
 * Dashboard tests — mock fetch, verify list renders with score + status.
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Dashboard } from "./Dashboard";
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Dashboard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders loading state initially", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)) // never resolves
    );
    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );
    expect(screen.getByTestId("dashboard-loading")).toBeDefined();
  });

  it("renders a table with analyzed companies showing score and status", async () => {
    const items = [
      {
        run_id: "run-1",
        report_id: "rep-1",
        company_name: "Acme Corp",
        domain: "acme.example",
        status: "complete",
        overall_score: 72,
        review_status: null,
        generated_at: "2026-05-26T10:00:00Z",
      },
      {
        run_id: "run-2",
        report_id: "rep-2",
        company_name: "Globex Ltd",
        domain: "globex.example",
        status: "complete",
        overall_score: 25,
        review_status: "approved",
        generated_at: "2026-05-25T09:00:00Z",
      },
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items, total: 2 }),
      })
    );

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-table")).toBeDefined();
    });

    // Company names visible
    expect(screen.getByText("Acme Corp")).toBeDefined();
    expect(screen.getByText("Globex Ltd")).toBeDefined();

    // Risk scores visible
    const scores = screen.getAllByTestId("risk-score");
    const scoreTexts = scores.map((el) => el.textContent);
    expect(scoreTexts).toContain("72");
    expect(scoreTexts).toContain("25");

    // Review statuses visible
    const badges = screen.getAllByTestId("review-status-badge");
    const badgeTexts = badges.map((el) => el.textContent);
    expect(badgeTexts).toContain("Pending Review");
    expect(badgeTexts).toContain("Approved");
  });

  it("filters by search, review status, and risk band", async () => {
    const items = [
      {
        run_id: "run-1",
        report_id: "rep-1",
        company_name: "Acme Corp",
        domain: "acme.example",
        status: "complete",
        overall_score: 72, // high, pending
        review_status: null,
        generated_at: "2026-05-26T10:00:00Z",
      },
      {
        run_id: "run-2",
        report_id: "rep-2",
        company_name: "Globex Ltd",
        domain: "globex.example",
        status: "complete",
        overall_score: 25, // low, reviewed
        review_status: "approved",
        generated_at: "2026-05-25T09:00:00Z",
      },
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items, total: 2 }),
      })
    );

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-table")).toBeDefined();
    });

    // Search narrows to Globex
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "globex" },
    });
    expect(screen.queryByText("Acme Corp")).toBeNull();
    expect(screen.getByText("Globex Ltd")).toBeDefined();

    // Clear search; filter to pending review → only Acme
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByTestId("filter-review"), {
      target: { value: "pending" },
    });
    expect(screen.getByText("Acme Corp")).toBeDefined();
    expect(screen.queryByText("Globex Ltd")).toBeNull();

    // Risk filter low + status all → only Globex
    fireEvent.change(screen.getByTestId("filter-review"), {
      target: { value: "all" },
    });
    fireEvent.change(screen.getByTestId("filter-risk"), {
      target: { value: "low" },
    });
    expect(screen.getByText("Globex Ltd")).toBeDefined();
    expect(screen.queryByText("Acme Corp")).toBeNull();

    // Combination that matches nothing → no-matches notice
    fireEvent.change(screen.getByTestId("filter-risk"), {
      target: { value: "high" },
    });
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "globex" },
    });
    expect(screen.getByTestId("dashboard-no-matches")).toBeDefined();
  });

  it("renders empty state when no reports exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [], total: 0 }),
      })
    );

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeDefined();
    });
  });

  it("renders error state on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: "Unauthorized" }),
      })
    );

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-error")).toBeDefined();
    });
  });
});
