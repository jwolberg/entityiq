import { render, screen, waitFor } from "@testing-library/react";
import { ActivityPanel } from "./ActivityPanel";
import { AuditLog } from "../pages/AuditLog";

const EVENTS = {
  events: [
    {
      id: "e1",
      event_type: "system.submission_received",
      actor: "demo-integration",
      description: "System submitted registration.",
      occurred_at: "2026-09-24T10:00:00Z",
      run_id: "run-1",
      payload: null,
    },
    {
      id: "e2",
      event_type: "operator.mark_reviewed",
      actor: "operator@demo.entityiq.dev",
      description: "Marked reviewed.",
      occurred_at: "2026-09-24T11:00:00Z",
      run_id: "run-1",
      payload: null,
    },
  ],
};

describe("ActivityPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the run's audit events with actor", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => EVENTS });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActivityPanel runId="run-1" token="tok" />);

    await waitFor(() => expect(screen.getAllByTestId("activity-event")).toHaveLength(2));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/audit/runs/run-1");
    expect(screen.getByText("operator@demo.entityiq.dev")).toBeDefined();
    expect(screen.getByText("Marked reviewed.")).toBeDefined();
  });

  it("degrades to a message when the audit API fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    );
    render(<ActivityPanel runId="run-1" token="tok" />);
    await waitFor(() => expect(screen.getByTestId("activity-error")).toBeDefined());
  });
});

describe("AuditLog page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the global log", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => EVENTS });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuditLog token="tok" onOpenRun={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(2));
    expect(fetchMock.mock.calls[0][0]).toContain("/api/audit/events");
  });
});
