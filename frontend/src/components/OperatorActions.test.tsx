/**
 * OperatorActions tests:
 *   - re-run analysis posts and surfaces the new run id
 *   - correct-and-re-run sends only changed fields and offers the new run
 *   - add-notes posts and confirms
 *   - export button fetches the export endpoint
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { OperatorActions } from "./OperatorActions";

afterEach(() => {
  vi.restoreAllMocks();
});

function lastFetchCall(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls[mock.mock.calls.length - 1];
}

describe("OperatorActions", () => {
  it("re-runs analysis and shows the new run id", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        new_run_id: "run-new",
        supersedes_run_id: "run-1",
        entity_id: "ent-1",
        status: "pending",
        triggered_at: "2026-05-31T10:00:00Z",
        message: "ok",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);
    const onOpenRun = vi.fn();

    render(
      <OperatorActions
        runId="run-1"
        token="t"
        submittedValues={{}}
        onOpenRun={onOpenRun}
      />
    );

    fireEvent.click(screen.getByTestId("rerun-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("new-run-banner")).toBeDefined();
    });
    // POSTed to the reanalysis endpoint
    expect(lastFetchCall(mockFetch)[0]).toContain("/reanalysis/run-1");

    // Opening the new run delegates to onOpenRun
    fireEvent.click(screen.getByTestId("open-new-run-btn"));
    expect(onOpenRun).toHaveBeenCalledWith("run-new");
  });

  it("correct-and-re-run sends only changed fields", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        new_run_id: "run-corrected",
        supersedes_run_id: "run-1",
        submission_id: "sub-1",
        corrections_applied: { domain: "fixed.com" },
        triggered_at: "2026-05-31T10:00:00Z",
        message: "ok",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    render(
      <OperatorActions
        runId="run-1"
        token="t"
        submittedValues={{ company_name: "Acme", domain: "acme.com" }}
      />
    );

    fireEvent.click(screen.getByTestId("toggle-correct-btn"));
    // Change only the domain field
    fireEvent.change(screen.getByTestId("correct-input-domain"), {
      target: { value: "fixed.com" },
    });
    fireEvent.click(screen.getByTestId("submit-correct-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("new-run-banner")).toBeDefined();
    });

    const [url, opts] = lastFetchCall(mockFetch);
    expect(url).toContain("/workflow/runs/run-1/correct");
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.corrections).toEqual({ domain: "fixed.com" });
  });

  it("blocks correct-and-re-run when nothing changed", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    render(
      <OperatorActions
        runId="run-1"
        token="t"
        submittedValues={{ domain: "acme.com" }}
      />
    );

    fireEvent.click(screen.getByTestId("toggle-correct-btn"));
    fireEvent.click(screen.getByTestId("submit-correct-btn"));

    expect(screen.getByTestId("action-error")).toBeDefined();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("adds a note and confirms", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        review_id: "rev-1",
        run_id: "run-1",
        notes: "looks legit",
        review_status: "reviewed",
        updated_at: "2026-05-31T10:00:00Z",
        message: "ok",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    render(
      <OperatorActions runId="run-1" token="t" submittedValues={{}} />
    );

    fireEvent.change(screen.getByTestId("note-input"), {
      target: { value: "looks legit" },
    });
    fireEvent.click(screen.getByTestId("add-note-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("note-saved")).toBeDefined();
    });
    expect(lastFetchCall(mockFetch)[0]).toContain("/workflow/runs/run-1/notes");
  });

  it("export button fetches the export endpoint", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ run_id: "run-1", report_id: "rep-1" }),
    });
    vi.stubGlobal("fetch", mockFetch);
    // jsdom has no createObjectURL — provide a no-op so the handler completes.
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:x"),
      revokeObjectURL: vi.fn(),
    });
    // Stub the download anchor so the real .click() doesn't trigger jsdom
    // navigation noise; we only care that the export endpoint was hit.
    const realCreate = document.createElement.bind(document);
    const fakeAnchor = realCreate("a") as HTMLAnchorElement;
    fakeAnchor.click = vi.fn();
    vi.spyOn(document, "createElement").mockImplementation((tag: string) =>
      tag === "a" ? fakeAnchor : realCreate(tag)
    );

    render(
      <OperatorActions runId="run-1" token="t" submittedValues={{}} />
    );

    fireEvent.click(screen.getByTestId("export-btn"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    expect(lastFetchCall(mockFetch)[0]).toContain("/reports/run-1/export");
    expect(fakeAnchor.click).toHaveBeenCalled();
  });
});
