/**
 * Monitoring alerts in the Individuals UI (ticket 0052).
 */

import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../../auth/AuthContext";
import { IndividualsQueue } from "./IndividualsQueue";
import { IndividualDetail } from "./IndividualDetail";

const value = { auth: { token: "t", operatorId: "op", role: "operator" }, signOut: vi.fn() };
function Wrapper({ children }: { children: ReactNode }) {
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function stub(bodies: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    const key = Object.keys(bodies).find((k) => url.startsWith(`/api${k}`));
    return { ok: true, json: async () => (key ? bodies[key] : {}) };
  }));
  return calls;
}

const ITEM = { run_id: "m1", subject_name: "Nadia Bouhaddou", status: "complete", trigger: "monitoring",
  system_disposition: "REVIEW", auto_closed: false, top_score: 0.5, human_disposition: null, created_at: null };

describe("monitoring alerts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("labels monitoring runs in the queue and filters to them via the API", async () => {
    const calls = stub({ "/screenings": { items: [ITEM] } });
    render(<Wrapper><IndividualsQueue onSelect={vi.fn()} /></Wrapper>);
    await waitFor(() => expect(screen.getByTestId("screening-row")).toBeDefined());
    expect(within(screen.getByTestId("screening-row")).getByTestId("monitoring-label")).toBeDefined();
    fireEvent.change(screen.getByTestId("filter-trigger"), { target: { value: "monitoring" } });
    await waitFor(() => expect(calls.some((c) => c.includes("trigger=monitoring"))).toBe(true));
  });

  it("shows the trigger and links to the prior decision", async () => {
    stub({ "/screenings/m1": {
      run_id: "m1", subject: { name: "Nadia Bouhaddou" }, subject_shredded: false, kyb_entity_id: null,
      run: { status: "complete", trigger: "monitoring", started_at: null, finished_at: null,
             duration_seconds: null, stages: {} },
      decision: { decision_id: "d", system_disposition: "REVIEW", auto_closed: false, top_score: 0.5,
                  rule_version: 1, thresholds: { clear_below: 0.35, match_at: 0.9 },
                  snapshot_ids: ["snap-2"], normalizer_version: "n1", created_at: null },
      candidates: [], dispositions: [],
      monitoring: { snapshot_id: "snap-2", source: "ofac_sdn", retrieved_at: "2026-09-02T00:00:00Z",
                    changed_entry_ids: ["2"], prior_run_id: "r-first", prior_disposition: "CLEAR" },
    } });
    const onOpenRun = vi.fn();
    render(<Wrapper><IndividualDetail runId="m1" onBack={vi.fn()} onOpenRun={onOpenRun} /></Wrapper>);
    await waitFor(() => expect(screen.getByTestId("monitoring-banner")).toBeDefined());
    const banner = screen.getByTestId("monitoring-banner");
    expect(banner.textContent).toContain("ofac_sdn");
    expect(banner.textContent).toContain("snap-2");
    expect(banner.textContent).toContain("2");
    expect(banner.textContent).toContain("CLEAR");
    fireEvent.click(within(banner).getByTestId("open-prior-run"));
    expect(onOpenRun).toHaveBeenCalledWith("r-first");
  });
});
