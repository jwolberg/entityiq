/**
 * OwnershipPanel tests (ticket 0003):
 *   - lists existing challenges
 *   - issues a DNS TXT challenge and shows instructions
 *   - email method requires a target before issuing
 *   - verifying a challenge updates its status
 *   - a failed verification keeps it pending and shows the message
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { OwnershipPanel } from "./OwnershipPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

function lastFetchCall(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls[mock.mock.calls.length - 1];
}

describe("OwnershipPanel", () => {
  it("shows an empty state when no challenges exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [] })
    );

    render(<OwnershipPanel runId="run-1" token="t" />);

    await waitFor(() => {
      expect(screen.getByTestId("ownership-empty")).toBeDefined();
    });
  });

  it("lists an existing pending challenge with its DNS instructions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            challenge_id: "chal-1",
            run_id: "run-1",
            submission_id: "sub-1",
            domain: "acme.example",
            method: "dns_txt",
            token: "abc123",
            status: "pending",
            issued_at: "2026-09-24T00:00:00Z",
            verified_at: null,
            instructions: {
              method: "dns_txt",
              dns_record_type: "TXT",
              dns_record_name: "acme.example",
              dns_record_value: "entityiq-domain-verification=abc123",
            },
            message: "Challenge issued.",
          },
        ],
      })
    );

    render(<OwnershipPanel runId="run-1" token="t" />);

    await waitFor(() => {
      expect(screen.getByTestId("ownership-item-chal-1")).toBeDefined();
    });
    expect(screen.getByTestId("ownership-status-chal-1").textContent).toBe(
      "pending"
    );
    expect(screen.getByText("entityiq-domain-verification=abc123")).toBeDefined();
    expect(screen.getByTestId("verify-challenge-btn-chal-1")).toBeDefined();
  });

  it("issues a DNS TXT challenge and shows it in the list", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [] }) // initial list
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          challenge_id: "chal-new",
          run_id: "run-1",
          submission_id: "sub-1",
          domain: "acme.example",
          method: "dns_txt",
          token: "tok-new",
          status: "pending",
          issued_at: "2026-09-24T00:00:00Z",
          verified_at: null,
          instructions: {
            method: "dns_txt",
            dns_record_type: "TXT",
            dns_record_name: "acme.example",
            dns_record_value: "entityiq-domain-verification=tok-new",
          },
          message: "Challenge issued.",
        }),
      });
    vi.stubGlobal("fetch", mockFetch);

    render(<OwnershipPanel runId="run-1" token="t" />);

    await waitFor(() => {
      expect(screen.getByTestId("ownership-empty")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("issue-challenge-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("ownership-item-chal-new")).toBeDefined();
    });
    const [url, opts] = lastFetchCall(mockFetch);
    expect(url).toContain("/ownership/runs/run-1/challenges");
    expect(JSON.parse((opts as RequestInit).body as string)).toEqual({
      method: "dns_txt",
    });
  });

  it("disables issuing an email challenge until a target is entered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [] })
    );

    render(<OwnershipPanel runId="run-1" token="t" />);
    await waitFor(() => screen.getByTestId("ownership-empty"));

    fireEvent.change(screen.getByTestId("ownership-method-select"), {
      target: { value: "email" },
    });
    expect(screen.getByTestId("issue-challenge-btn")).toHaveProperty(
      "disabled",
      true
    );

    fireEvent.change(screen.getByTestId("ownership-email-target"), {
      target: { value: "verify@acme.example" },
    });
    expect(screen.getByTestId("issue-challenge-btn")).toHaveProperty(
      "disabled",
      false
    );
  });

  it("verifying a challenge updates its status to verified", async () => {
    const pendingChallenge = {
      challenge_id: "chal-2",
      run_id: "run-1",
      submission_id: "sub-1",
      domain: "acme.example",
      method: "dns_txt",
      token: "abc123",
      status: "pending",
      issued_at: "2026-09-24T00:00:00Z",
      verified_at: null,
      instructions: {
        method: "dns_txt",
        dns_record_type: "TXT",
        dns_record_name: "acme.example",
        dns_record_value: "entityiq-domain-verification=abc123",
      },
      message: "Challenge issued.",
    };
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [pendingChallenge] })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          challenge_id: "chal-2",
          method: "dns_txt",
          verified: true,
          status: "verified",
          verified_at: "2026-09-24T01:00:00Z",
          message: "Domain ownership verified — added as a bounded confidence signal.",
        }),
      });
    vi.stubGlobal("fetch", mockFetch);

    render(<OwnershipPanel runId="run-1" token="t" />);
    await waitFor(() => screen.getByTestId("ownership-item-chal-2"));

    fireEvent.click(screen.getByTestId("verify-challenge-btn-chal-2"));

    await waitFor(() => {
      expect(screen.getByTestId("ownership-status-chal-2").textContent).toBe(
        "verified"
      );
    });
    expect(screen.queryByTestId("verify-challenge-btn-chal-2")).toBeNull();
    const [url] = lastFetchCall(mockFetch);
    expect(url).toContain("/ownership/challenges/chal-2/verify");
  });

  it("a failed verification keeps the challenge pending and shows the message", async () => {
    const pendingChallenge = {
      challenge_id: "chal-3",
      run_id: "run-1",
      submission_id: "sub-1",
      domain: "acme.example",
      method: "dns_txt",
      token: "abc123",
      status: "pending",
      issued_at: "2026-09-24T00:00:00Z",
      verified_at: null,
      instructions: {
        method: "dns_txt",
        dns_record_type: "TXT",
        dns_record_name: "acme.example",
        dns_record_value: "entityiq-domain-verification=abc123",
      },
      message: "Challenge issued.",
    };
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [pendingChallenge] })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          challenge_id: "chal-3",
          method: "dns_txt",
          verified: false,
          status: "pending",
          verified_at: null,
          message: "Not verified yet — token not found or incorrect.",
        }),
      });
    vi.stubGlobal("fetch", mockFetch);

    render(<OwnershipPanel runId="run-1" token="t" />);
    await waitFor(() => screen.getByTestId("ownership-item-chal-3"));

    fireEvent.click(screen.getByTestId("verify-challenge-btn-chal-3"));

    await waitFor(() => {
      expect(screen.getByTestId("ownership-result-chal-3")).toBeDefined();
    });
    expect(screen.getByTestId("ownership-status-chal-3").textContent).toBe(
      "pending"
    );
    expect(screen.getByTestId("verify-challenge-btn-chal-3")).toBeDefined();
  });
});
