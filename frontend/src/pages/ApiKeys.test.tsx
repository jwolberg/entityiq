/**
 * ApiKeys tests — mock fetch, verify create/list/revoke flows (ticket 0026).
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ApiKeys } from "./ApiKeys";

const TOKEN = "lead-token";

describe("ApiKeys", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists existing keys with prefix and metadata only", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/api-clients") {
          return {
            ok: true,
            json: async () => ({
              items: [
                {
                  id: "cl-1",
                  name: "platform-onboarding",
                  key_prefix: "eiq_ab12cd34",
                  active: true,
                  created_at: "2026-09-01T00:00:00Z",
                  last_used_at: null,
                },
              ],
              total: 1,
            }),
          };
        }
        return { ok: true, json: async () => ({}) };
      })
    );

    render(<ApiKeys token={TOKEN} />);

    await waitFor(() =>
      expect(screen.getByText("platform-onboarding")).toBeDefined()
    );
    expect(screen.getByText("eiq_ab12cd34…")).toBeDefined();
    // Never the hash or a full key in the list view.
    expect(screen.queryByText(/key_hash/i)).toBeNull();
  });

  it("creates a key and reveals the full key exactly once", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      if (url === "/api/api-clients" && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            id: "cl-2",
            name: "new-integration",
            key_prefix: "eiq_zz99yy88",
            api_key: "eiq_zz99yy88-full-secret-value",
            active: true,
            created_at: "2026-09-24T00:00:00Z",
          }),
        };
      }
      if (url === "/api/api-clients") {
        return { ok: true, json: async () => ({ items: [], total: 0 }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ApiKeys token={TOKEN} />);
    await waitFor(() => expect(screen.getByText("No API keys yet.")).toBeDefined());

    fireEvent.change(screen.getByTestId("api-key-name-input"), {
      target: { value: "new-integration" },
    });
    fireEvent.click(screen.getByTestId("create-api-key-button"));

    await waitFor(() =>
      expect(screen.getByTestId("new-api-key-value").textContent).toBe(
        "eiq_zz99yy88-full-secret-value"
      )
    );

    const createCall = fetchMock.mock.calls.find(
      ([url, opts]) => url === "/api/api-clients" && (opts as RequestInit)?.method === "POST"
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toEqual({
      name: "new-integration",
    });

    // Dismissing clears the one-time reveal.
    fireEvent.click(screen.getByTestId("dismiss-new-api-key"));
    expect(screen.queryByTestId("new-api-key-reveal")).toBeNull();
  });

  it("revokes a key", async () => {
    let active = true;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/api-clients/cl-1/revoke") {
        active = false;
        return {
          ok: true,
          json: async () => ({
            id: "cl-1",
            name: "platform-onboarding",
            active: false,
            message: "API client revoked.",
          }),
        };
      }
      if (url === "/api/api-clients") {
        return {
          ok: true,
          json: async () => ({
            items: [
              {
                id: "cl-1",
                name: "platform-onboarding",
                key_prefix: "eiq_ab12cd34",
                active,
                created_at: "2026-09-01T00:00:00Z",
                last_used_at: null,
              },
            ],
            total: 1,
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ApiKeys token={TOKEN} />);
    await waitFor(() => expect(screen.getByTestId("revoke-api-key-cl-1")).toBeDefined());

    fireEvent.click(screen.getByTestId("revoke-api-key-cl-1"));

    await waitFor(() => expect(screen.getByText("Revoked")).toBeDefined());
    expect(screen.queryByTestId("revoke-api-key-cl-1")).toBeNull();
  });

  it("shows an error when creation fails (e.g. duplicate name)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (url === "/api/api-clients" && opts?.method === "POST") {
          return {
            ok: false,
            status: 409,
            json: async () => ({ detail: "An API client named 'dup' already exists." }),
          };
        }
        return { ok: true, json: async () => ({ items: [], total: 0 }) };
      })
    );

    render(<ApiKeys token={TOKEN} />);
    await waitFor(() => expect(screen.getByText("No API keys yet.")).toBeDefined());

    fireEvent.change(screen.getByTestId("api-key-name-input"), {
      target: { value: "dup" },
    });
    fireEvent.click(screen.getByTestId("create-api-key-button"));

    await waitFor(() =>
      expect(screen.getByTestId("create-api-key-error").textContent).toMatch(/already exists/)
    );
  });
});
