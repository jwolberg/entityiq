/* eslint-disable react-refresh/only-export-components */
/**
 * Auth context — minimal session management for the operator app.
 *
 * Stores the Bearer token in React state (not localStorage, keeping it
 * simple for the MVP).  The sign-in form renders when no token is present;
 * once signed in the child app is mounted.
 */

import {
  createContext,
  useContext,
  useState,
  ReactNode,
  FormEvent,
} from "react";
import { apiClient } from "../api/client";

interface AuthState {
  token: string;
  operatorId: string;
  role: string;
}

interface AuthContextValue {
  // Non-null by construction: AuthProvider renders the sign-in form (not the
  // context) until a session exists, so any consumer of this context is
  // guaranteed an authenticated session.
  auth: AuthState;
  signOut: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// Sign-in form
// ---------------------------------------------------------------------------

interface SignInFormProps {
  onSuccess: (auth: AuthState) => void;
}

function SignInForm({ onSuccess }: SignInFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const resp = await apiClient.signIn({ email, password });
      onSuccess({
        token: resp.session_token,
        operatorId: resp.operator_id,
        role: resp.role,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={styles.page}>
      <div style={styles.overlay}>
        <div style={styles.card}>
          <h1 style={styles.title}>EntityIQ</h1>
        <p style={styles.subtitle}>Operator Sign In</p>
        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.field}>
            <label htmlFor="email" style={styles.label}>
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              style={styles.input}
              data-testid="email-input"
            />
          </div>
          <div style={styles.field}>
            <label htmlFor="password" style={styles.label}>
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              style={styles.input}
              data-testid="password-input"
            />
          </div>
          {error && (
            <p style={styles.error} role="alert" data-testid="sign-in-error">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={loading}
            style={styles.button}
            data-testid="sign-in-button"
          >
            {loading ? "Signing in…" : "Sign In"}
          </button>
        </form>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [auth, setAuth] = useState<AuthState | null>(null);

  function signOut() {
    setAuth(null);
  }

  if (!auth) {
    return <SignInForm onSuccess={setAuth} />;
  }

  return (
    <AuthContext.Provider value={{ auth, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Minimal inline styles (no Tailwind in this MVP)
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  page: {
    position: "relative",
    minHeight: "100vh",
    width: "100%",
    backgroundColor: "#0b0f17",
    backgroundImage:
      "radial-gradient(circle at 20% 20%, #1e3a5f 0%, transparent 55%), " +
      "radial-gradient(circle at 80% 80%, #2d1f4f 0%, transparent 50%)",
  },
  // Fixed, full-viewport layer that keeps the prompt centered.
  overlay: {
    position: "fixed",
    inset: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "1rem",
    pointerEvents: "none",
  },
  card: {
    pointerEvents: "auto",
    backgroundColor: "#ffffff",
    padding: "2rem",
    borderRadius: "0.75rem",
    boxShadow: "0 12px 48px rgba(0,0,0,0.45)",
    width: "100%",
    maxWidth: "400px",
  },
  title: {
    margin: "0 0 0.25rem",
    fontSize: "1.5rem",
    fontWeight: 700,
    color: "#111827",
  },
  subtitle: {
    margin: "0 0 1.5rem",
    color: "#6b7280",
    fontSize: "0.95rem",
  },
  form: {
    display: "flex",
    flexDirection: "column",
    gap: "1rem",
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: "0.25rem",
  },
  label: {
    fontSize: "0.875rem",
    fontWeight: 500,
    color: "#374151",
  },
  input: {
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "1rem",
    outline: "none",
  },
  error: {
    color: "#dc2626",
    fontSize: "0.875rem",
    margin: 0,
  },
  button: {
    padding: "0.625rem 1rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    border: "none",
    borderRadius: "0.375rem",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
