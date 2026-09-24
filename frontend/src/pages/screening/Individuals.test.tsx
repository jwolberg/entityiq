/**
 * Individuals queue + detail (ticket 0043, PRD-IDV §[16]).
 */

import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../../auth/AuthContext";
import { IndividualsQueue } from "./IndividualsQueue";
import { IndividualDetail, SCREENING_POLL_MS } from "./IndividualDetail";

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
    expect(within(cand).getByText("ofac_sdn:9001@snap-ofac-1#names")).toBeDefined();
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
