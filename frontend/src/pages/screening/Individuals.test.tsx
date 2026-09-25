/**
 * Individuals queue + detail (ticket 0043, PRD-IDV §[16]).
 */

import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../../auth/AuthContext";
import { IndividualsQueue } from "./IndividualsQueue";
import { IndividualDetail, SCREENING_POLL_MS } from "./IndividualDetail";
import { EXPLANATION } from "../../components/explanationFixture";

function withRole(role: string) {
  const value = { auth: { token: "t", operatorId: "op", role }, signOut: vi.fn() };
  return function Wrapper({ children }: { children: ReactNode }) {
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  };
}

function routed(routes: Record<string, unknown[] | unknown>) {
  const calls: string[] = [];
  const queues = Object.fromEntries(
    Object.entries(routes).map(([k, v]) => [k, Array.isArray(v) ? [...v] : [v]])
  );
  const mock = vi.fn(async (url: string, opts?: RequestInit) => {
    calls.push(`${opts?.method ?? "GET"} ${url}`);
    const key = Object.keys(queues).find((k) => url.startsWith(`/api${k}`));
    const q = key ? queues[key] : [{}];
    const body = q.length > 1 ? q.shift() : q[0];
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal("fetch", mock);
  return calls;
}

const QUEUE = {
  items: [
    { run_id: "r-match", subject_name: "Teodor Vasilescu", status: "complete", trigger: "intake",
      system_disposition: "MATCH", auto_closed: false, top_score: 0.9, human_disposition: null, created_at: null },
    { run_id: "r-review", subject_name: "Helena Lindqvistad", status: "complete", trigger: "intake",
      system_disposition: "REVIEW", auto_closed: false, top_score: 0.5, human_disposition: null, created_at: null },
    { run_id: "r-clear", subject_name: "Chidi Okafor", status: "complete", trigger: "intake",
      system_disposition: "CLEAR", auto_closed: true, top_score: null, human_disposition: null, created_at: null },
  ],
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "r-match",
    subject: { name: "Teodor Vasilescu", dob: "1962-08-30", nationality: "RO" },
    subject_shredded: false,
    kyb_entity_id: null,
    run: { status: "complete", trigger: "intake", started_at: "2026-09-24T12:00:00Z",
           finished_at: "2026-09-24T12:00:02Z", duration_seconds: 2,
           stages: { block_candidates: "complete", score_candidates: "complete", dispose: "complete" } },
    decision: { decision_id: "d1", system_disposition: "MATCH", auto_closed: false, top_score: 0.55,
                rule_version: 3, thresholds: { clear_below: 0.35, match_at: 0.9 },
                snapshot_ids: ["snap-ofac-1"], normalizer_version: "n1", created_at: null },
    candidates: [{
      candidate_id: "c1", score: 0.55, band: "REVIEW", blocking_keys: ["mp:TTR"],
      record: { id: "w1", source: "ofac_sdn", source_entry_id: "9001", snapshot_id: "snap-ofac-1",
                primary_name: "Teodor VASILESCU", names: [{ name: "Teodor VASILESCU", kind: "primary" }],
                dobs: [{ date: "1970-01-01" }], pobs: [], nationalities: ["RO"], documents: [], program: "SDGT" },
      terms: [
        { name: "name_exact_normalized", weight: 0.5, subject_field: "name", record_field: "names",
          record_value: "Teodor VASILESCU", claim_ids: ["s1", "c-rec-1"] },
        { name: "dob_conflict", weight: -0.35, subject_field: "dob", record_field: "dobs",
          record_value: [{ date: "1970-01-01" }], claim_ids: ["s2", "c-rec-2"] },
      ],
      claims: {
        "c-rec-1": { about: "record", field: "names", value: "Teodor VASILESCU", source: "ofac_sdn",
                     locator: "ofac_sdn:9001@snap-ofac-1#names", retrieved_at: null },
        "c-rec-2": { about: "record", field: "dobs", value: null, source: "ofac_sdn",
                     locator: "ofac_sdn:9001@snap-ofac-1#dobs", retrieved_at: null },
      },
    }],
    dispositions: [],
    ...overrides,
  };
}

describe("IndividualsQueue", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists runs most severe first with disposition badges", async () => {
    routed({ "/screenings": QUEUE });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualsQueue onSelect={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getAllByTestId("screening-row")).toHaveLength(3));
    const rows = screen.getAllByTestId("screening-row");
    expect(rows.map((r) => within(r).getByTestId("screening-badge").textContent)).toEqual([
      "MATCH", "REVIEW", "CLEAR (auto)",
    ]);
  });

  it("filters by disposition through the API", async () => {
    const calls = routed({ "/screenings": [QUEUE, { items: [QUEUE.items[1]] }] });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualsQueue onSelect={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getAllByTestId("screening-row")).toHaveLength(3));
    fireEvent.change(screen.getByTestId("filter-disposition"), { target: { value: "REVIEW" } });
    await waitFor(() => expect(screen.getAllByTestId("screening-row")).toHaveLength(1));
    expect(calls.some((c) => c.includes("disposition=REVIEW"))).toBe(true);
  });

  it("pages through a long queue with Load more", async () => {
    const extra = { ...QUEUE.items[2], run_id: "r-clear-2", subject_name: "Ada Obi" };
    const calls = routed({
      "/screenings": [
        { items: QUEUE.items, total: 4, limit: 3, offset: 0 },
        { items: [extra], total: 4, limit: 3, offset: 3 },
      ],
    });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualsQueue onSelect={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getAllByTestId("screening-row")).toHaveLength(3));
    expect(screen.getByTestId("queue-count").textContent).toBe("Showing 3 of 4");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByTestId("screening-row")).toHaveLength(4));
    expect(calls.some((c) => c.includes("offset=3"))).toBe(true);
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });
});

describe("IndividualDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows subject vs candidate, every term with evidence, conflicts beside matches", async () => {
    routed({ "/screenings/r-match": detail() });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getByTestId("candidate-c1")).toBeDefined());
    const cand = screen.getByTestId("candidate-c1");
    expect(within(cand).getByText("Teodor VASILESCU", { selector: "[data-testid='record-name']" })).toBeDefined();
    expect(within(cand).getByTestId("term-name_exact_normalized").textContent).toContain("+0.5");
    expect(within(cand).getByTestId("term-dob_conflict").textContent).toContain("-0.35");
    expect(within(cand).getByTestId("term-dob_conflict").getAttribute("data-tone")).toBe("conflict");
    // Raw locators moved to the evidence panel (ticket 0070).
    expect(cand.textContent).not.toContain("ofac_sdn:9001@snap-ofac-1#names");
    expect(screen.getByTestId("decision-meta").textContent).toContain("rule v3");
    expect(screen.getByTestId("decision-meta").textContent).toContain("snap-ofac-1");
  });

  it("shows coverage gaps for unavailable sources", async () => {
    routed({ "/screenings/r-match": detail({
      run: { ...detail().run, stages: { block_candidates: "unavailable", dispose: "complete" } },
    }) });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getByTestId("coverage-gaps").textContent).toContain("block_candidates"));
  });

  it("hides the disposition form from examiners and offers replay to lead/examiner only", async () => {
    routed({ "/screenings/r-match": detail() });
    const Examiner = withRole("examiner");
    const { unmount } = render(<Examiner><IndividualDetail runId="r-match" onBack={vi.fn()} /></Examiner>);
    await waitFor(() => expect(screen.getByTestId("replay-button")).toBeDefined());
    expect(screen.queryByTestId("disposition-form")).toBeNull();
    unmount();

    routed({ "/screenings/r-match": detail() });
    const Operator = withRole("operator");
    render(<Operator><IndividualDetail runId="r-match" onBack={vi.fn()} /></Operator>);
    await waitFor(() => expect(screen.getByTestId("disposition-form")).toBeDefined());
    expect(screen.queryByTestId("replay-button")).toBeNull();
  });

  it("records a disposition and shows the replay result", async () => {
    const calls = routed({
      "/screenings/r-match/disposition": { disposition_id: "x" },
      "/screenings/r-match/replay": { reproduced: true, shredded: false, differences: [],
        original: { disposition: "MATCH" }, replayed: { disposition: "MATCH" } },
      "/screenings/r-match": [detail(), detail({ dispositions: [{ disposition: "CLEAR",
        notes: "different person", operator_id: "op", created_at: null }] })],
    });
    const Lead = withRole("lead");
    render(<Lead><IndividualDetail runId="r-match" onBack={vi.fn()} /></Lead>);
    await waitFor(() => expect(screen.getByTestId("disposition-form")).toBeDefined());
    fireEvent.change(screen.getByTestId("disposition-notes"), { target: { value: "different person" } });
    fireEvent.click(screen.getByTestId("dispose-CLEAR"));
    await waitFor(() => expect(screen.getByTestId("disposition-history").textContent).toContain("different person"));
    expect(calls.some((c) => c.startsWith("POST /api/screenings/r-match/disposition"))).toBe(true);

    fireEvent.click(screen.getByTestId("replay-button"));
    await waitFor(() => expect(screen.getByTestId("replay-result").textContent).toContain("Reproduced"));
  });

  it("polls an in-flight run and stops once it completes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = detail({ decision: null, candidates: [],
      run: { ...detail().run, status: "running", finished_at: null, duration_seconds: null } });
    const calls = routed({ "/screenings/r-match": [running, detail()] });
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getByTestId("screening-in-progress")).toBeDefined());
    await vi.advanceTimersByTimeAsync(SCREENING_POLL_MS);
    await waitFor(() => expect(screen.getByTestId("candidate-c1")).toBeDefined());
    await vi.advanceTimersByTimeAsync(SCREENING_POLL_MS * 3);
    expect(calls.filter((c) => c === "GET /api/screenings/r-match")).toHaveLength(2);
  });
});

describe("IndividualDetail evidence panel (ticket 0070)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  // Explanation first: routed() matches by prefix.
  const routes = () => ({
    "/screenings/r-match/explanation": EXPLANATION,
    "/screenings/r-match": detail(),
  });

  it("opens from the header button and records the deep link", async () => {
    const calls = routed(routes());
    const Wrapper = withRole("examiner");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    fireEvent.click(await screen.findByTestId("why-open"));
    expect(await screen.findByRole("dialog")).toBeDefined();
    await screen.findByTestId("why-step-disposition");
    expect(calls).toContain("GET /api/screenings/r-match/explanation");
    expect(window.location.search).toBe("?why=1");

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.location.search).toBe("");
  });

  it("opens from a candidate's Why? link, focused on that candidate", async () => {
    routed(routes());
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    fireEvent.click(await screen.findByTestId("why-cand-link-c1"));
    const target = await screen.findByTestId("why-cand-c1");
    expect(target.dataset.focused).toBe("true");
    expect(window.location.search).toBe("?why=1&candidate=c1");
  });

  it("opens on load from a ?why=1&candidate= link", async () => {
    window.history.replaceState({}, "", "/?why=1&candidate=c2");
    routed(routes());
    const Wrapper = withRole("examiner");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    const target = await screen.findByTestId("why-cand-c2");
    expect(target.dataset.focused).toBe("true");
  });

  it("clears the deep link when leaving the page with the panel open", async () => {
    routed(routes());
    const Wrapper = withRole("operator");
    const { unmount } = render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    fireEvent.click(await screen.findByTestId("why-open"));
    await screen.findByRole("dialog");
    expect(window.location.search).toBe("?why=1");
    unmount();
    // Otherwise the next run opened would pop the panel open on its own.
    expect(window.location.search).toBe("");
  });

  it("refreshes the open panel after a replay", async () => {
    const calls = routed({
      "/screenings/r-match/explanation": EXPLANATION,
      "/screenings/r-match/replay": { reproduced: true, shredded: false, differences: [],
        original: { disposition: "MATCH" }, replayed: { disposition: "MATCH" } },
      "/screenings/r-match": detail(),
    });
    const Lead = withRole("lead");
    render(<Lead><IndividualDetail runId="r-match" onBack={vi.fn()} /></Lead>);
    fireEvent.click(await screen.findByTestId("why-open"));
    await screen.findByTestId("why-step-human");
    const before = calls.filter((c) => c.endsWith("/explanation")).length;
    fireEvent.click(screen.getByTestId("replay-button"));
    await waitFor(() =>
      expect(calls.filter((c) => c.endsWith("/explanation")).length).toBe(before + 1)
    );
  });

  it("keeps the disposition form usable with the panel open", async () => {
    routed(routes());
    const Wrapper = withRole("operator");
    render(<Wrapper><IndividualDetail runId="r-match" onBack={vi.fn()} /></Wrapper>);
    fireEvent.click(await screen.findByTestId("why-open"));
    await screen.findByRole("dialog");
    expect(screen.getByTestId("disposition-form")).toBeDefined();
    expect((screen.getByTestId("dispose-CLEAR") as HTMLButtonElement).disabled).toBe(false);
  });
});
