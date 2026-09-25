/**
 * ApiKeys — lead-only integration API-key provisioning (ticket 0026).
 * The API enforces lead-only access (403 for operators); the nav link is
 * hidden for operators in App.tsx, same pattern as AuditLog.
 *
 * Create shows the full plaintext key exactly once — it is never returned
 * again by the API, so this page is the only place it's ever visible.
 */

import { FormEvent, useEffect, useState } from "react";
import { apiClient, ApiClientListItem } from "../api/client";

interface ApiKeysProps {
  token: string;
}

export function ApiKeys({ token }: ApiKeysProps) {
  const [items, setItems] = useState<ApiClientListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [justCreatedKey, setJustCreatedKey] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  function refresh() {
    apiClient
      .listApiClients(token)
      .then((r) => setItems(r.items))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load")
      );
  }

  useEffect(refresh, [token]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setCreateError(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setCreateError("Name is required.");
      return;
    }
    setCreating(true);
    try {
      const resp = await apiClient.createApiClient({ name: trimmed }, token);
      setJustCreatedKey(resp.api_key);
      setName("");
      refresh();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create key");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: string) {
    setRevokingId(id);
    try {
      await apiClient.revokeApiClient(id, token);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    } finally {
      setRevokingId(null);
    }
  }

  if (error) return <p role="alert">Could not load API keys: {error}</p>;

  return (
    <div>
      <h2 style={styles.title}>Integration API Keys</h2>
      <p style={styles.note}>
        Keys authenticate integrating systems at the API boundary. The full
        key is shown once, right after creation — copy it now.
      </p>

      <form onSubmit={handleCreate} style={styles.form}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="System name, e.g. platform-onboarding"
          style={styles.input}
          data-testid="api-key-name-input"
        />
        <button
          type="submit"
          disabled={creating}
          style={styles.createBtn}
          data-testid="create-api-key-button"
        >
          {creating ? "Creating…" : "Create Key"}
        </button>
      </form>
      {createError && (
        <p style={styles.error} role="alert" data-testid="create-api-key-error">
          {createError}
        </p>
      )}

      {justCreatedKey && (
        <div style={styles.revealBox} data-testid="new-api-key-reveal">
          <p style={styles.revealLabel}>
            Copy this key now — it will not be shown again.
          </p>
          <code style={styles.revealKey} data-testid="new-api-key-value">
            {justCreatedKey}
          </code>
          <button
            type="button"
            onClick={() => setJustCreatedKey(null)}
            style={styles.dismissBtn}
            data-testid="dismiss-new-api-key"
          >
            I've copied it — dismiss
          </button>
        </div>
      )}

      {items === null ? (
        <p>Loading API keys…</p>
      ) : items.length === 0 ? (
        <p style={styles.note}>No API keys yet.</p>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Name</th>
              <th style={styles.th}>Prefix</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}>Created</th>
              <th style={styles.th}>Last used</th>
              <th style={styles.th}></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} data-testid="api-key-row">
                <td style={styles.td}>{item.name}</td>
                <td style={styles.td}>
                  <code>{item.key_prefix}…</code>
                </td>
                <td style={styles.td}>
                  {item.active ? "Active" : "Revoked"}
                </td>
                <td style={styles.td}>
                  {new Date(item.created_at).toLocaleString()}
                </td>
                <td style={styles.td}>
                  {item.last_used_at
                    ? new Date(item.last_used_at).toLocaleString()
                    : "Never"}
                </td>
                <td style={styles.td}>
                  {item.active && (
                    <button
                      type="button"
                      onClick={() => handleRevoke(item.id)}
                      disabled={revokingId === item.id}
                      style={styles.revokeBtn}
                      data-testid={`revoke-api-key-${item.id}`}
                    >
                      {revokingId === item.id ? "Revoking…" : "Revoke"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  title: { margin: "0 0 0.25rem" },
  note: { margin: "0 0 1rem", color: "#6b7280", fontSize: "0.875rem" },
  form: { display: "flex", gap: "0.5rem", marginBottom: "0.5rem" },
  input: {
    flex: "1 1 320px",
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
  },
  createBtn: {
    padding: "0.5rem 1rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    border: "none",
    borderRadius: "0.375rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  error: { color: "#dc2626", fontSize: "0.875rem", margin: "0 0 1rem" },
  revealBox: {
    padding: "1rem",
    backgroundColor: "#fffbeb",
    border: "1px solid #fcd34d",
    borderRadius: "0.375rem",
    marginBottom: "1.5rem",
  },
  revealLabel: { margin: "0 0 0.5rem", fontWeight: 600, color: "#92400e" },
  revealKey: {
    display: "block",
    padding: "0.5rem",
    backgroundColor: "#ffffff",
    border: "1px solid #fcd34d",
    borderRadius: "0.25rem",
    wordBreak: "break-all",
    marginBottom: "0.5rem",
  },
  dismissBtn: {
    padding: "0.375rem 0.75rem",
    backgroundColor: "#ffffff",
    border: "1px solid #d97706",
    color: "#92400e",
    borderRadius: "0.375rem",
    cursor: "pointer",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" },
  th: {
    textAlign: "left",
    padding: "0.5rem",
    borderBottom: "2px solid #e5e7eb",
    color: "#374151",
  },
  td: { padding: "0.5rem", borderBottom: "1px solid #f3f4f6", verticalAlign: "top" },
  revokeBtn: {
    padding: "0.25rem 0.75rem",
    backgroundColor: "#ffffff",
    border: "1px solid #dc2626",
    color: "#dc2626",
    borderRadius: "0.25rem",
    cursor: "pointer",
    fontSize: "0.8rem",
  },
};
