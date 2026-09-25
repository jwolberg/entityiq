import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, ScreeningExplanation } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getScreeningExplanation (ticket 0068)", () => {
  it("GETs the run's explanation with the bearer token", async () => {
    const body: Partial<ScreeningExplanation> = { run_id: "r1", steps: [], citations: {} };
    const mock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", mock);

    const result = await apiClient.getScreeningExplanation("r1", "tok");

    expect(result.run_id).toBe("r1");
    const [url, opts] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/screenings/r1/explanation");
    expect((opts.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(opts.method ?? "GET").toBe("GET");
  });

  it("surfaces the API's detail message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "The run has no decision yet." }), { status: 409 })
      )
    );
    await expect(apiClient.getScreeningExplanation("r1", "tok")).rejects.toThrow(
      "The run has no decision yet."
    );
  });
});
