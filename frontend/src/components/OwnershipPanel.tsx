/**
 * OwnershipPanel — issue a domain-ownership challenge and check its status
 * (ticket 0003, plan unit U24).
 *
 * Wraps the ticket-0003 backend endpoints:
 *   - Issue challenge  → POST /ownership/runs/{run_id}/challenges
 *   - Verify challenge → POST /ownership/challenges/{challenge_id}/verify
 *   - List challenges  → GET  /ownership/runs/{run_id}/challenges
 *
 * A verified challenge only ever adds a small, bounded confidence signal —
 * it is optional, and never an authorization decision (PRD § Domain
 * Ownership Verification; USERS § 4).
 */

import { useEffect, useState } from "react";
import { apiClient, OwnershipChallenge, OwnershipMethod } from "../api/client";

interface OwnershipPanelProps {
  runId: string;
  token: string;
}

const METHOD_LABELS: Record<OwnershipMethod, string> = {
  dns_txt: "DNS TXT record",
  html_meta: "HTML meta tag",
  email: "Email",
};

export function OwnershipPanel({ runId, token }: OwnershipPanelProps) {
  const [challenges, setChallenges] = useState<OwnershipChallenge[]>([]);
  const [loading, setLoading] = useState(true);
  const [method, setMethod] = useState<OwnershipMethod>("dns_txt");
  const [emailTarget, setEmailTarget] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const [submittedTokens, setSubmittedTokens] = useState<Record<string, string>>(
    {}
  );
  const [verifyResult, setVerifyResult] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiClient
      .listOwnershipChallenges(runId, token)
      .then((rows) => {
        // Defensive: only accept a well-formed array (an unrelated/unmocked
        // response should surface as empty, never crash the detail page).
        if (!cancelled) setChallenges(Array.isArray(rows) ? rows : []);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load challenges"
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, token]);

  async function handleIssue() {
    setIssuing(true);
    setError(null);
    try {
      const body =
        method === "email" ? { method, target: emailTarget.trim() } : { method };
      const challenge = await apiClient.issueOwnershipChallenge(runId, body, token);
      setChallenges((prev) => [challenge, ...prev]);
      setEmailTarget("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to issue challenge");
    } finally {
      setIssuing(false);
    }
  }

  async function handleVerify(challenge: OwnershipChallenge) {
    setVerifyingId(challenge.challenge_id);
    setError(null);
    try {
      const body =
        challenge.method === "email"
          ? { submitted_token: submittedTokens[challenge.challenge_id] ?? "" }
          : {};
      const resp = await apiClient.verifyOwnershipChallenge(
        challenge.challenge_id,
        body,
        token
      );
      setChallenges((prev) =>
        prev.map((c) =>
          c.challenge_id === challenge.challenge_id
            ? { ...c, status: resp.status, verified_at: resp.verified_at }
            : c
        )
      );
      setVerifyResult((prev) => ({
        ...prev,
        [challenge.challenge_id]: resp.message,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setVerifyingId(null);
    }
  }

  return (
    <div data-testid="ownership-panel">
      <p style={styles.note}>
        Optional: verify that the registrant controls the claimed domain. A
        verified challenge adds a bounded confidence signal — it does not
        authorize the registrant to represent the organization.
      </p>

      <div style={styles.issueRow}>
        <select
          value={method}
          onChange={(e) => setMethod(e.target.value as OwnershipMethod)}
          disabled={issuing}
          style={styles.select}
          data-testid="ownership-method-select"
        >
          <option value="dns_txt">DNS TXT record</option>
          <option value="html_meta">HTML meta tag</option>
          <option value="email">Email</option>
        </select>
        {method === "email" && (
          <input
            type="email"
            placeholder="verify@company.com"
            value={emailTarget}
            onChange={(e) => setEmailTarget(e.target.value)}
            disabled={issuing}
            style={styles.input}
            data-testid="ownership-email-target"
          />
        )}
        <button
          onClick={handleIssue}
          disabled={issuing || (method === "email" && !emailTarget.trim())}
          style={styles.btn}
          data-testid="issue-challenge-btn"
        >
          {issuing ? "Issuing…" : "Issue Challenge"}
        </button>
      </div>

      {error && (
        <p style={styles.error} role="alert" data-testid="ownership-error">
          {error}
        </p>
      )}

      {loading ? (
        <p style={styles.note} data-testid="ownership-loading">
          Loading challenges…
        </p>
      ) : challenges.length === 0 ? (
        <p style={styles.note} data-testid="ownership-empty">
          No challenges issued yet.
        </p>
      ) : (
        <ul style={styles.list} data-testid="ownership-challenge-list">
          {challenges.map((c) => (
            <li
              key={c.challenge_id}
              style={styles.item}
              data-testid={`ownership-item-${c.challenge_id}`}
            >
              <div style={styles.itemHeader}>
                <strong>{METHOD_LABELS[c.method]}</strong>
                <span
                  style={{
                    ...styles.statusBadge,
                    ...(c.status === "verified"
                      ? styles.statusVerified
                      : styles.statusPending),
                  }}
                  data-testid={`ownership-status-${c.challenge_id}`}
                >
                  {c.status}
                </span>
              </div>

              {c.status !== "verified" && c.method === "dns_txt" && (
                <code style={styles.code}>{c.instructions.dns_record_value}</code>
              )}
              {c.status !== "verified" && c.method === "html_meta" && (
                <code style={styles.code}>{c.instructions.html_snippet}</code>
              )}
              {c.status !== "verified" && c.method === "email" && (
                <>
                  <p style={styles.note}>
                    Sent to {c.instructions.email_target}. Paste the token the
                    registrant received:
                  </p>
                  <input
                    type="text"
                    value={submittedTokens[c.challenge_id] ?? ""}
                    onChange={(e) =>
                      setSubmittedTokens((prev) => ({
                        ...prev,
                        [c.challenge_id]: e.target.value,
                      }))
                    }
                    style={styles.input}
                    data-testid={`ownership-submitted-token-${c.challenge_id}`}
                  />
                </>
              )}

              {c.status !== "verified" && (
                <button
                  onClick={() => handleVerify(c)}
                  disabled={verifyingId === c.challenge_id}
                  style={styles.btnSecondary}
                  data-testid={`verify-challenge-btn-${c.challenge_id}`}
                >
                  {verifyingId === c.challenge_id
                    ? "Checking…"
                    : "Check Verification"}
                </button>
              )}
              {verifyResult[c.challenge_id] && (
                <p
                  style={styles.note}
                  data-testid={`ownership-result-${c.challenge_id}`}
                >
                  {verifyResult[c.challenge_id]}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  note: {
    margin: "0 0 0.75rem",
    fontSize: "0.8rem",
    color: "#6b7280",
  },
  issueRow: {
    display: "flex",
    flexWrap: "wrap",
    gap: "0.5rem",
    alignItems: "center",
    marginBottom: "0.75rem",
  },
  select: {
    padding: "0.4rem 0.6rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
  },
  input: {
    padding: "0.4rem 0.6rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    flex: "1 1 12rem",
  },
  btn: {
    padding: "0.5rem 1rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    border: "none",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  btnSecondary: {
    padding: "0.4rem 0.8rem",
    backgroundColor: "#ffffff",
    color: "#2563eb",
    border: "1px solid #2563eb",
    borderRadius: "0.375rem",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  error: {
    color: "#dc2626",
    fontSize: "0.85rem",
    margin: "0 0 0.5rem",
  },
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  item: {
    padding: "0.75rem 1rem",
    backgroundColor: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
  },
  itemHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "0.4rem",
  },
  statusBadge: {
    display: "inline-block",
    padding: "0.15rem 0.6rem",
    borderRadius: "9999px",
    fontSize: "0.7rem",
    fontWeight: 600,
    textTransform: "capitalize" as React.CSSProperties["textTransform"],
  },
  statusVerified: {
    color: "#166534",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
  },
  statusPending: {
    color: "#92400e",
    backgroundColor: "#fffbeb",
    border: "1px solid #fcd34d",
  },
  code: {
    display: "block",
    padding: "0.4rem 0.6rem",
    backgroundColor: "#f3f4f6",
    borderRadius: "0.25rem",
    fontSize: "0.75rem",
    overflowWrap: "break-word",
    marginBottom: "0.5rem",
  },
};
