/**
 * DecisionEvidencePanel (ticket 0069): the "Why this decision?" sidebar.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DecisionEvidencePanel } from "./DecisionEvidencePanel";
import { EXPLANATION } from "./explanationFixture";
import { readWhyParams } from "./whyParams";

function mockFetch(respond: () => Response) {
  const mock = vi.fn(async () => respond());
  vi.stubGlobal("fetch", mock);
  return mock;
}

const ok = (body: unknown) => () => new Response(JSON.stringify(body), { status: 200 });

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A page with a trigger button that opens the panel, like IndividualDetail. */
function Harness(props: { decided?: boolean; focus?: string | null; refreshKey?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(true)}>Why this decision?</button>
      {open && (
        <DecisionEvidencePanel
          runId="r1"
          token="t"
          decided={props.decided ?? true}
          focusCandidateId={props.focus ?? null}
          refreshKey={props.refreshKey ?? 0}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

describe("DecisionEvidencePanel", () => {
  it("is a labelled, non-modal dialog that fetches the explanation", async () => {
    const mock = mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("false");
    const labelledBy = dialog.getAttribute("aria-labelledby")!;
    expect(document.getElementById(labelledBy)?.textContent).toMatch(/why this decision/i);
    await screen.findByText(/most severe candidate band: MATCH/);
    expect((mock.mock.calls[0] as unknown as [string])[0]).toBe("/api/screenings/r1/explanation");
  });

  it("walks every step in order", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    await screen.findByTestId("why-step-disposition");
    const kinds = screen.getAllByTestId(/^why-step-/).map((el) => el.dataset.testid);
    expect(kinds).toEqual([
      "why-step-sources", "why-step-blocking", "why-step-candidates",
      "why-step-disposition", "why-step-human",
    ]);
    expect(screen.getByTestId("why-step-sources").textContent).toMatch(/UK OFSI Consolidated List.*unavailable/s);
    expect(screen.getByTestId("why-step-blocking").textContent).toMatch(
      /2 list records matched at least part of the subject's name/
    );
    expect(screen.getByTestId("why-step-blocking").textContent).not.toMatch(/name key|capped/);
    expect(screen.getByTestId("why-step-disposition").textContent).toMatch(/list:uk_ofsi/);
    expect(screen.getByTestId("why-step-human").textContent).toMatch(/confirmed on passport/);
    expect(screen.getByTestId("why-step-human").textContent).toMatch(/Reproduced/);
  });

  it("shows each candidate's band reason and its terms with citation chips", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    const cand = await screen.findByTestId("why-cand-c1");
    expect(cand.textContent).toMatch(/Teodor Vasilescu/);
    expect(cand.textContent).toMatch(/at or above the match threshold/);
    const term = screen.getByTestId("why-term-c1-dob_conflict");
    expect(term.dataset.tone).toBe("conflict");
    expect(term.textContent).toMatch(/Full dates of birth differ/);

    const chip = screen.getAllByTestId("citation-rec-dobs")[0];
    expect(chip.textContent).toBe("OFAC SDN List · entry 1234 · dobs · list as of 2026-09-20");
    expect(chip.getAttribute("aria-label")).toMatch(/OFAC SDN List, entry 1234, field dobs/);
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    const detail = screen.getByTestId("citation-detail-rec-dobs");
    expect(detail.textContent).toMatch(/abcdef012345/);
    expect(detail.textContent).toMatch(/7534|ofac_sdn:1234@s1#dobs/);
    expect(detail.querySelector("a")?.getAttribute("href")).toBe(
      "https://www.treasury.gov/ofac/downloads/sdn.csv"
    );
    expect(screen.getAllByTestId("citation-sub-dob")[0].textContent).toBe("Subject submission · dob");
  });

  it("closes on Esc and returns focus to the trigger", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    const trigger = screen.getByText("Why this decision?");
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("ignores Esc pressed outside the panel (it's non-modal)", async () => {
    mockFetch(ok(EXPLANATION));
    render(<><textarea data-testid="notes" /><Harness /></>);
    fireEvent.click(screen.getByText("Why this decision?"));
    await screen.findByRole("dialog");
    fireEvent.keyDown(screen.getByTestId("notes"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeNull();
  });

  it("closes from its close button", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    fireEvent.click(await screen.findByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("marks and moves focus to the targeted candidate", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness focus="c2" />);
    fireEvent.click(screen.getByText("Why this decision?"));
    const c2 = await screen.findByTestId("why-cand-c2");
    expect(c2.dataset.focused).toBe("true");
    expect(screen.getByTestId("why-cand-c1").dataset.focused).toBe("false");
    await waitFor(() => expect(document.activeElement).toBe(c2));
  });

  it("doesn't steal focus back on a background refresh", async () => {
    mockFetch(ok(EXPLANATION));
    function Page() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <textarea data-testid="notes" />
          <button onClick={() => setTick((n) => n + 1)}>tick</button>
          <DecisionEvidencePanel runId="r1" token="t" decided focusCandidateId="c2"
            refreshKey={tick} onClose={() => {}} />
        </>
      );
    }
    render(<Page />);
    const c2 = await screen.findByTestId("why-cand-c2");
    await waitFor(() => expect(document.activeElement).toBe(c2));
    const notes = screen.getByTestId("notes");
    notes.focus();
    await act(async () => {
      fireEvent.click(screen.getByText("tick"));
    });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(document.activeElement).toBe(notes);
  });

  it("shows list values as plain text, not JSON", async () => {
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    const term = await screen.findByTestId("why-term-c1-dob_conflict");
    expect(term.textContent).toContain("List value: 1962-08-31");
    expect(term.textContent).not.toMatch(/[{}"[\]]/);
  });

  it("confirms when the snapshot hash is copied", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    mockFetch(ok(EXPLANATION));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    fireEvent.click((await screen.findAllByTestId("citation-rec-dobs"))[0]);
    const copy = screen.getByRole("button", { name: /copy/i });
    await act(async () => {
      fireEvent.click(copy);
    });
    expect(writeText).toHaveBeenCalledWith("abcdef0123456789abcdef");
    expect(copy.textContent).toBe("Copied");
  });

  it("fails soft when the explanation can't load, and can retry", async () => {
    let fail = true;
    const mock = mockFetch(() =>
      fail
        ? new Response(JSON.stringify({ detail: "boom" }), { status: 500 })
        : new Response(JSON.stringify(EXPLANATION), { status: 200 })
    );
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    expect((await screen.findByTestId("why-error")).textContent).toMatch(
      /Decision explanation is unavailable right now\./
    );
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await screen.findByTestId("why-step-disposition");
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it("says the decision isn't made yet while the run is in flight, without fetching", async () => {
    const mock = mockFetch(ok(EXPLANATION));
    render(<Harness decided={false} />);
    fireEvent.click(screen.getByText("Why this decision?"));
    expect((await screen.findByTestId("why-pending")).textContent).toMatch(/not made yet/i);
    expect(mock).not.toHaveBeenCalled();
  });

  it("says when the subject was shredded but still shows the list evidence", async () => {
    mockFetch(ok({ ...EXPLANATION, subject_shredded: true }));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    expect(await screen.findByTestId("why-shredded")).toBeTruthy();
    expect(screen.getByTestId("why-cand-c1")).toBeTruthy();
  });

  it("explains a run with no candidates", async () => {
    const steps = EXPLANATION.steps.map((s) =>
      s.kind === "blocking" ? { ...s, candidate_count: 0, candidates: [] }
        : s.kind === "scoring" || s.kind === "banding" ? { ...s, candidates: [] }
        : s.kind === "disposition"
          ? { ...s, system_disposition: "CLEAR" as const, auto_closed: true, reason_code: "no_candidates",
              reason_text: "No list record matched any part of the subject's name, and every required list was available and fresh, so the run closed as CLEAR without a human.",
              coverage_gaps: [] }
          : s
    );
    mockFetch(ok({ ...EXPLANATION, steps }));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    expect((await screen.findByTestId("why-step-blocking")).textContent).toMatch(
      /No list record matched any part of the subject's name/
    );
    expect((screen.getByTestId("why-step-disposition")).textContent).toMatch(/without a human/);
  });

  it("warns when today's rules disagree with the stored decision", async () => {
    const steps = EXPLANATION.steps.map((s) =>
      s.kind === "banding" ? { ...s, candidates: s.candidates.map((c) => ({ ...c, consistent: false })) } : s
    );
    mockFetch(ok({ ...EXPLANATION, steps }));
    render(<Harness />);
    fireEvent.click(screen.getByText("Why this decision?"));
    const drift = (await screen.findByTestId("why-drift")).textContent;
    expect(drift).toMatch(/This decision was made under rule v1, normalizer n2/);
    expect(drift).toMatch(/today's rules/);
  });

  it("refetches when the page's refresh tick changes", async () => {
    const mock = mockFetch(ok(EXPLANATION));
    function Ticking() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <button onClick={() => setTick((n) => n + 1)}>tick</button>
          <DecisionEvidencePanel runId="r1" token="t" decided focusCandidateId={null}
            refreshKey={tick} onClose={() => {}} />
        </>
      );
    }
    render(<Ticking />);
    await screen.findByTestId("why-step-disposition");
    await act(async () => {
      fireEvent.click(screen.getByText("tick"));
    });
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
  });
});

describe("readWhyParams (deep link)", () => {
  it("opens for ?why=1 and targets a candidate", () => {
    expect(readWhyParams("?why=1&candidate=c2")).toEqual({ open: true, candidate: "c2" });
    expect(readWhyParams("?why=1")).toEqual({ open: true, candidate: null });
    expect(readWhyParams("")).toEqual({ open: false, candidate: null });
    expect(readWhyParams("?candidate=c2")).toEqual({ open: false, candidate: null });
  });
});
