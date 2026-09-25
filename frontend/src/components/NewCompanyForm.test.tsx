/**
 * NewCompanyForm — declared officers and owners (ticket 0079).
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NewCompanyForm } from "./NewCompanyForm";

afterEach(() => {
  vi.restoreAllMocks();
});

function fillRequired() {
  for (const [id, v] of [
    ["field-company_name", "Acme Corp"],
    ["field-work_email", "cto@acme.example"],
    ["field-company_domain", "acme.example"],
    ["field-country", "US"],
  ]) {
    fireEvent.change(screen.getByTestId(id), { target: { value: v } });
  }
}

function stubSubmit() {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    json: async () => ({
      submission_id: "s", run_id: "r", status: "pending", is_free_email_domain: false, message: "ok",
    }),
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentBody(fetchMock: { mock: { calls: unknown[][] } }) {
  const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(opts.body as string);
}

describe("NewCompanyForm people", () => {
  it("sends declared officers and owners, skipping blank and removed rows", async () => {
    const fetchMock = stubSubmit();
    const onSuccess = vi.fn();
    render(<NewCompanyForm token="t" onClose={vi.fn()} onSuccess={onSuccess} />);
    fillRequired();

    fireEvent.click(screen.getByTestId("add-person"));
    fireEvent.change(screen.getByTestId("person-0-name"), { target: { value: "Ann Lee" } });
    fireEvent.change(screen.getByTestId("person-0-role"), { target: { value: "Director" } });

    fireEvent.click(screen.getByTestId("add-person"));
    fireEvent.change(screen.getByTestId("person-1-name"), { target: { value: "Bo Chen" } });
    fireEvent.change(screen.getByTestId("person-1-relationship"), { target: { value: "owner" } });
    fireEvent.change(screen.getByTestId("person-1-ownership_pct"), { target: { value: "60" } });
    fireEvent.change(screen.getByTestId("person-1-dob"), { target: { value: "1971-04" } });

    fireEvent.click(screen.getByTestId("add-person")); // left blank → not sent
    fireEvent.click(screen.getByTestId("add-person"));
    fireEvent.change(screen.getByTestId("person-3-name"), { target: { value: "Removed Person" } });
    fireEvent.click(screen.getByTestId("remove-person-3"));

    fireEvent.click(screen.getByTestId("submit-new-company"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());

    expect(sentBody(fetchMock).people).toEqual([
      { name: "Ann Lee", relationship: "officer", role: "Director" },
      { name: "Bo Chen", relationship: "owner", ownership_pct: 60, dob: "1971-04" },
    ]);
  });

  it("omits people when none are entered", async () => {
    const fetchMock = stubSubmit();
    const onSuccess = vi.fn();
    render(<NewCompanyForm token="t" onClose={vi.fn()} onSuccess={onSuccess} />);
    fillRequired();
    fireEvent.click(screen.getByTestId("submit-new-company"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(sentBody(fetchMock)).not.toHaveProperty("people");
  });
});
