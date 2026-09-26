/**
 * Dashboard tests — mock fetch, verify list renders with score + status.
 */

import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
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

  it("shows the triage tier so an escalated low-score company isn't shown as safe", async () => {
    const items = [
      {
        run_id: "run-v",
        report_id: "rep-v",
        company_name: "Volga Maritime",
        domain: "volga.example",
        status: "complete",
        overall_score: 22,
        triage_tier: "escalate",
        review_status: null,
        generated_at: "2026-09-24T10:00:00Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ items, total: 1 }) })
    );
    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByTestId("triage-badge")).toBeDefined());
    expect(screen.getByTestId("triage-badge").textContent).toBe("Escalate");
    expect(screen.getByText("22").getAttribute("style")).toContain("rgb(220, 38, 38)");
  });

  it("closes the form on submit and shows a background notice until the report lands", async () => {
    const newItem = {
      run_id: "run-new",
      report_id: "rep-new",
      company_name: "Acme Corp",
      domain: "acme.example",
      status: "complete",
      overall_score: 10,
      triage_tier: "approve_eligible",
      review_status: null,
      generated_at: "2026-09-25T10:00:00Z",
    };
    let listCalls = 0;
    const mockFetch = vi.fn(async (url: string, opts?: RequestInit) => {
      if (url.endsWith("/submissions") && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            submission_id: "sub-new",
            run_id: "run-new",
            status: "pending",
            is_free_email_domain: false,
            message: "ok",
          }),
        };
      }
      if (url.includes("/reports/run-new")) {
        return { ok: true, json: async () => ({ run_id: "run-new", run: { status: "complete", started_at: null,
          finished_at: null, duration_seconds: null, stages: {} } }) };
      }
      listCalls += 1;
      // Initial load and the first poll: not there yet; then it lands.
      const items = listCalls >= 3 ? [newItem] : [];
      return { ok: true, json: async () => ({ items, total: items.length }) };
    });
    vi.stubGlobal("fetch", mockFetch);

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} pollMs={10} />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByTestId("open-new-company-form")).toBeDefined());
    fireEvent.click(screen.getByTestId("open-new-company-form"));
    for (const [id, v] of [
      ["field-company_name", "Acme Corp"],
      ["field-work_email", "cto@acme.example"],
      ["field-company_domain", "acme.example"],
      ["field-country", "US"],
    ]) {
      fireEvent.change(screen.getByTestId(id), { target: { value: v } });
    }
    fireEvent.click(screen.getByTestId("submit-new-company"));

    const notice = await screen.findByTestId("background-run-notice");
    expect(screen.queryByTestId("new-company-form")).toBeNull();
    expect(notice.textContent).toContain("Acme Corp");
    expect(notice.textContent).toContain("running in the background");

    await waitFor(() =>
      expect(screen.getByTestId("background-run-notice").textContent).toContain("ready")
    );
    expect(within(screen.getByRole("table")).getByText("Acme Corp")).toBeDefined();
    const pollsWhenReady = listCalls;
    await new Promise((r) => setTimeout(r, 50));
    expect(listCalls).toBe(pollsWhenReady); // polling stops once it lands

    fireEvent.click(screen.getByTestId("dismiss-background-notice"));
    expect(screen.queryByTestId("background-run-notice")).toBeNull();
  });

  async function submitAcme() {
    await waitFor(() => expect(screen.getByTestId("open-new-company-form")).toBeDefined());
    fireEvent.click(screen.getByTestId("open-new-company-form"));
    for (const [id, v] of [
      ["field-company_name", "Acme Corp"],
      ["field-work_email", "cto@acme.example"],
      ["field-company_domain", "acme.example"],
      ["field-country", "US"],
    ]) {
      fireEvent.change(screen.getByTestId(id), { target: { value: v } });
    }
    fireEvent.click(screen.getByTestId("submit-new-company"));
    await screen.findByTestId("background-run-notice");
  }

  const SUBMITTED = {
    ok: true,
    json: async () => ({ submission_id: "sub-new", run_id: "run-new", status: "pending",
      is_free_email_domain: false, message: "ok" }),
  };

  it("says the run failed when its report shows a failed run", async () => {
    const landed = { run_id: "run-new", report_id: "rep-new", company_name: "Acme Corp",
      domain: "acme.example", status: "complete", overall_score: null, triage_tier: null,
      review_status: null, generated_at: "2026-09-25T10:00:00Z" };
    let listCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, opts?: RequestInit) => {
      if (opts?.method === "POST") return SUBMITTED;
      if (url.includes("/reports/run-new")) {
        return { ok: true, json: async () => ({ run_id: "run-new", run: { status: "failed", started_at: null,
          finished_at: null, duration_seconds: null, stages: {} } }) };
      }
      listCalls += 1;
      const items = listCalls >= 2 ? [landed] : [];
      return { ok: true, json: async () => ({ items, total: items.length }) };
    }));
    render(<Wrapper><Dashboard onSelect={vi.fn()} pollMs={10} /></Wrapper>);
    await submitAcme();
    await waitFor(() =>
      expect(screen.getByTestId("background-run-notice").textContent).toContain("failed")
    );
  });

  it("stops polling and says so when the queue can't be refreshed", async () => {
    let listCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, opts?: RequestInit) => {
      if (opts?.method === "POST") return SUBMITTED;
      listCalls += 1;
      if (listCalls >= 2) return { ok: false, status: 401, json: async () => ({ detail: "Not authenticated" }) };
      return { ok: true, json: async () => ({ items: [], total: 0 }) };
    }));
    render(<Wrapper><Dashboard onSelect={vi.fn()} pollMs={10} /></Wrapper>);
    await submitAcme();
    await waitFor(() =>
      expect(screen.getByTestId("background-run-notice").textContent).toContain("Couldn't check")
    );
    const calls = listCalls;
    await new Promise((r) => setTimeout(r, 50));
    expect(listCalls).toBe(calls); // stopped
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

  // A stand-in for GET /reports that applies the same filters and paging as
  // the backend (ticket 0089), and records every URL it was called with.
  const last = (urls: URL[]) => urls[urls.length - 1];

  function fakeReportsServer(all: Record<string, unknown>[]) {
    const urls: URL[] = [];
    const fetchMock = vi.fn(async (input: string) => {
      const url = new URL(input, "http://test");
      urls.push(url);
      const p = url.searchParams;
      const q = (p.get("q") ?? "").toLowerCase();
      const matches = all.filter((i) => {
        if (q && !`${i.company_name} ${i.domain}`.toLowerCase().includes(q)) return false;
        if (p.get("triage_tier") && i.triage_tier !== p.get("triage_tier")) return false;
        if (p.get("review_status") === "pending" && i.review_status !== null) return false;
        if (p.get("review_status") === "reviewed" && i.review_status === null) return false;
        return true;
      });
      const limit = Number(p.get("limit") ?? 50);
      const offset = Number(p.get("offset") ?? 0);
      const items = matches.slice(offset, offset + limit);
      return { ok: true, json: async () => ({ items, total: matches.length, limit, offset }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    return urls;
  }

  it("filters by search, review status, and triage tier on the server", async () => {
    const urls = fakeReportsServer([
      {
        run_id: "run-1",
        report_id: "rep-1",
        company_name: "Acme Corp",
        domain: "acme.example",
        status: "complete",
        overall_score: 72,
        triage_tier: "review", // high score, but tier is "review" not "escalate"
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
        triage_tier: "pre_clear",
        review_status: "approved",
        generated_at: "2026-05-25T09:00:00Z",
      },
      {
        run_id: "run-3",
        report_id: "rep-3",
        company_name: "Volga Maritime",
        domain: "volga.example",
        status: "complete",
        overall_score: 22, // low score, but a sanctions hit forces escalate
        triage_tier: "escalate",
        review_status: null,
        generated_at: "2026-05-24T09:00:00Z",
      },
    ]);

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} searchDebounceMs={0} />
      </Wrapper>
    );
    await screen.findByTestId("dashboard-table");
    expect(urls[0].search).toBe(""); // first page, no filters

    // Search narrows to Globex — sent to the server as q
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "globex" },
    });
    await waitFor(() => expect(screen.queryByText("Acme Corp")).toBeNull());
    expect(screen.getByText("Globex Ltd")).toBeDefined();
    expect(last(urls).searchParams.get("q")).toBe("globex");

    // Clear search; filter to pending review → Acme + Volga
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByTestId("filter-review"), {
      target: { value: "pending" },
    });
    await waitFor(() => expect(screen.queryByText("Globex Ltd")).toBeNull());
    expect(screen.getByText("Acme Corp")).toBeDefined();
    expect(screen.getByText("Volga Maritime")).toBeDefined();
    expect(last(urls).searchParams.get("review_status")).toBe("pending");
    expect(last(urls).searchParams.has("q")).toBe(false);

    // Tier filter "escalate" + status all → only Volga, even at a low score
    fireEvent.change(screen.getByTestId("filter-review"), {
      target: { value: "all" },
    });
    fireEvent.change(screen.getByTestId("filter-tier"), {
      target: { value: "escalate" },
    });
    await waitFor(() => expect(screen.queryByText("Acme Corp")).toBeNull());
    expect(screen.getByText("Volga Maritime")).toBeDefined();
    expect(last(urls).searchParams.get("triage_tier")).toBe("escalate");
    expect(last(urls).searchParams.has("review_status")).toBe(false);

    // Combination that matches nothing → no-matches notice; filters stay usable
    fireEvent.change(screen.getByTestId("dashboard-search"), {
      target: { value: "globex" },
    });
    await screen.findByTestId("dashboard-no-matches");
    expect(screen.getByTestId("dashboard-filters")).toBeDefined();
    expect(screen.queryByTestId("dashboard-empty")).toBeNull();
  });

  it("loads the next page with Load more", async () => {
    const all = Array.from({ length: 60 }, (_, i) => ({
      run_id: `run-${i}`,
      report_id: `rep-${i}`,
      company_name: `Company ${i}`,
      domain: `c${i}.example`,
      status: "complete",
      overall_score: 10,
      triage_tier: "review",
      review_status: null,
      generated_at: "2026-05-26T10:00:00Z",
    }));
    const urls = fakeReportsServer(all);

    render(
      <Wrapper>
        <Dashboard onSelect={vi.fn()} />
      </Wrapper>
    );
    await screen.findByTestId("dashboard-table");
    expect(screen.getAllByRole("button", { name: /View details/ })).toHaveLength(50);
    expect(screen.getByTestId("dashboard-count").textContent).toBe("Showing 50 of 60");

    fireEvent.click(screen.getByTestId("load-more-reports"));
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /View details/ })).toHaveLength(60)
    );
    expect(last(urls).searchParams.get("offset")).toBe("50");
    expect(screen.getByTestId("dashboard-count").textContent).toBe("Showing 60 of 60");
    expect(screen.queryByTestId("load-more-reports")).toBeNull();
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
