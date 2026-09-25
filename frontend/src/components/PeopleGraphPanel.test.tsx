/**
 * PeopleGraphPanel — officers & owners around the company (ticket 0084).
 */

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { PeopleGraphPanel } from "./PeopleGraphPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

function person(overrides: Record<string, unknown>) {
  return {
    name: "Ann Lee",
    shredded: false,
    relationships: ["officer"],
    roles: [],
    sources: [{ source: "declared", locator: "submission" }],
    ownership_pct: null,
    company_person_ids: ["cp-1"],
    screening_subject_id: "subj-1",
    screening_run_id: "scr-1",
    disposition: "CLEAR",
    auto_closed: true,
    top_score: null,
    human_disposition: null,
    ...overrides,
  };
}

const PEOPLE = {
  run_id: "run-1",
  sources: { registry: "complete", screening: "complete" },
  people: [
    person({
      name: "Teodor Vasilescu",
      relationships: ["officer", "owner"],
      roles: ["director"],
      ownership_pct: 60,
      screening_run_id: "scr-match",
      disposition: "MATCH",
      auto_closed: false,
      top_score: 0.93,
      sources: [
        { source: "declared", locator: "submission" },
        { source: "registry", provider: "stub", locator: "stub:harbor/1" },
      ],
    }),
    person({ name: "Ann Lee", screening_run_id: "scr-clear" }),
    person({ name: null, shredded: true, screening_run_id: "scr-shred", disposition: "REVIEW" }),
  ],
};

function stub(body: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(onOpenScreening = vi.fn()) {
  render(
    <PeopleGraphPanel
      runId="run-1"
      token="t"
      companyName="Harbor Freight Lines"
      onOpenScreening={onOpenScreening}
    />
  );
  return onOpenScreening;
}

describe("PeopleGraphPanel", () => {
  it("draws the company with each person as a status-labelled node and edge", async () => {
    const fetchMock = stub(PEOPLE);
    renderPanel();

    const graph = await screen.findByTestId("people-graph");
    expect((fetchMock.mock.calls[0] as unknown[])[0]).toBe("/api/reports/run-1/people");
    expect(within(graph).getByText("Harbor Freight Lines")).toBeDefined();

    const nodes = screen.getAllByTestId("person-node");
    expect(nodes).toHaveLength(3);
    const teodor = nodes.find((n) => n.textContent?.includes("Teodor Vasilescu"))!;
    // Status is never color alone: label + glyph on the node.
    expect(teodor.textContent).toContain("MATCH");
    expect(teodor.textContent).toContain("!");
    // The edge says how they relate.
    const edges = screen.getAllByTestId("person-edge").map((e) => e.textContent);
    expect(edges).toContain("officer, owner · director · 60%");
    // A shredded person is still drawn, without a name.
    expect(nodes.some((n) => n.textContent?.includes("(shredded)"))).toBe(true);
    // Legend names every status shown.
    const legend = screen.getByTestId("people-legend").textContent;
    for (const label of ["MATCH", "REVIEW", "CLEAR"]) expect(legend).toContain(label);
  });

  it("opens a person's screening by click or keyboard", async () => {
    stub(PEOPLE);
    const onOpen = renderPanel();

    const nodes = await screen.findAllByTestId("person-node");
    const ann = nodes.find((n) => n.textContent?.includes("Ann Lee"))!;
    fireEvent.click(ann);
    expect(onOpen).toHaveBeenCalledWith("scr-clear");

    const teodor = nodes.find((n) => n.textContent?.includes("Teodor"))!;
    fireEvent.keyDown(teodor, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith("scr-match");
  });

  it("shows details on hover and focus", async () => {
    stub(PEOPLE);
    renderPanel();

    const nodes = await screen.findAllByTestId("person-node");
    const teodor = nodes.find((n) => n.textContent?.includes("Teodor"))!;
    fireEvent.mouseEnter(teodor);
    const tip = screen.getByTestId("person-tooltip").textContent!;
    expect(tip).toContain("Teodor Vasilescu");
    expect(tip).toContain("declared");
    expect(tip).toContain("registry");
    expect(tip).toContain("0.93");
    fireEvent.mouseLeave(teodor);
    expect(screen.queryByTestId("person-tooltip")).toBeNull();

    fireEvent.focus(teodor);
    expect(screen.getByTestId("person-tooltip")).toBeDefined();
  });

  it("offers a table view with the same people", async () => {
    stub(PEOPLE);
    const onOpen = renderPanel();

    await screen.findByTestId("people-graph");
    fireEvent.click(screen.getByRole("button", { name: "Table" }));

    const rows = screen.getAllByTestId("people-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Teodor Vasilescu");
    expect(rows[0].textContent).toContain("MATCH");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /open/i }));
    expect(onOpen).toHaveBeenCalledWith("scr-clear");
  });

  it("explains an empty result and any coverage gap", async () => {
    stub({ run_id: "run-1", sources: { registry: "unavailable", screening: "complete" }, people: [] });
    renderPanel();

    const empty = await screen.findByTestId("people-empty");
    expect(empty.textContent).toContain("No officers or owners");
    expect(screen.getByTestId("people-gap").textContent).toContain("registry");
  });

  it("flags when people were found but screening was unavailable", async () => {
    stub({ run_id: "run-1", sources: { registry: "complete", screening: "unavailable" }, people: [] });
    renderPanel();

    expect((await screen.findByTestId("people-gap")).textContent).toContain("not screened");
  });

  it("shows an error when the people can't be loaded", async () => {
    stub({ detail: "boom" }, false);
    renderPanel();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("boom"));
  });
});
