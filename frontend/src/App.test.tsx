import { render, screen } from "@testing-library/react";
import App from "./App";

// App now renders the sign-in form before auth is established.
// Mock fetch so the sign-in API call does not reach the network.
vi.stubGlobal("fetch", vi.fn());

describe("App", () => {
  it("renders the EntityIQ sign-in form before auth", () => {
    render(<App />);
    // The sign-in form contains an email input and submit button
    expect(screen.getByTestId("email-input")).toBeDefined();
    expect(screen.getByTestId("sign-in-button")).toBeDefined();
  });

  it("shows the platform name in the sign-in card", () => {
    render(<App />);
    // The sign-in card has an EntityIQ heading
    expect(screen.getByRole("heading", { name: /entityiq/i })).toBeDefined();
  });
});

describe("Sign out", () => {
  it("invalidates the session on the server, then returns to sign-in", async () => {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/auth/sign-in") {
        return {
          ok: true,
          json: async () => ({
            session_token: "tok-1",
            operator_id: "op-1",
            role: "operator",
          }),
        };
      }
      if (url === "/api/auth/sign-out") return { ok: true, status: 204 };
      return { ok: true, json: async () => ({ items: [], total: 0 }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.change(screen.getByTestId("email-input"), {
      target: { value: "op@example.com" },
    });
    fireEvent.change(screen.getByTestId("password-input"), {
      target: { value: "pw" },
    });
    fireEvent.click(screen.getByTestId("sign-in-button"));
    await waitFor(() => expect(screen.getByText("Sign Out")).toBeDefined());

    fireEvent.click(screen.getByText("Sign Out"));
    await waitFor(() => expect(screen.getByTestId("email-input")).toBeDefined());

    const signOutCall = fetchMock.mock.calls.find(
      ([url]) => url === "/api/auth/sign-out"
    ) as unknown as [string, RequestInit] | undefined;
    expect(signOutCall).toBeDefined();
    expect(
      (signOutCall![1].headers as Record<string, string>)["Authorization"]
    ).toBe("Bearer tok-1");
  });
});

describe("Lead-only audit log navigation", () => {
  async function signInAs(role: "lead" | "operator") {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/auth/sign-in") {
          return {
            ok: true,
            json: async () => ({ session_token: "t", operator_id: "o", role }),
          };
        }
        if (url.startsWith("/api/audit")) {
          return { ok: true, json: async () => ({ events: [] }) };
        }
        return { ok: true, json: async () => ({ items: [], total: 0 }) };
      })
    );
    render(<App />);
    fireEvent.change(screen.getByTestId("email-input"), { target: { value: "a@b.c" } });
    fireEvent.change(screen.getByTestId("password-input"), { target: { value: "pw" } });
    fireEvent.click(screen.getByTestId("sign-in-button"));
    await waitFor(() => expect(screen.getByText("Sign Out")).toBeDefined());
  }

  it("shows the Audit Log link to leads", async () => {
    await signInAs("lead");
    expect(screen.getByTestId("nav-audit-log")).toBeDefined();
  });

  it("hides the Audit Log link from operators", async () => {
    await signInAs("operator");
    expect(screen.queryByTestId("nav-audit-log")).toBeNull();
  });
});
