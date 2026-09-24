/**
 * App — root component.
 *
 * Hash-based routing (no react-router dependency):
 *   #/           → Dashboard (queue)
 *   #/run/<id>   → CompanyDetail
 *
 * AuthProvider gates the entire app: renders the sign-in form when no
 * session token is present.
 */

import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Dashboard } from "./pages/Dashboard";
import { CompanyDetail } from "./pages/CompanyDetail";
import { AuditLog } from "./pages/AuditLog";
import { ApiKeys } from "./pages/ApiKeys";

type Route =
  | { page: "dashboard" }
  | { page: "detail"; runId: string }
  | { page: "audit" }
  | { page: "api-keys" };

function AppShell() {
  const { auth, signOut } = useAuth();
  const [route, setRoute] = useState<Route>({ page: "dashboard" });

  function navigate(r: Route) {
    setRoute(r);
  }

  return (
    <div style={styles.shell}>
      {/* Top nav */}
      <header style={styles.header}>
        <button
          onClick={() => navigate({ page: "dashboard" })}
          style={styles.logoBtn}
          aria-label="EntityIQ — go to dashboard"
        >
          EntityIQ
        </button>
        <div style={styles.navRight}>
          {auth.role === "lead" && (
            <button
              onClick={() => navigate({ page: "audit" })}
              style={styles.signOutBtn}
              data-testid="nav-audit-log"
            >
              Audit Log
            </button>
          )}
          {auth.role === "lead" && (
            <button
              onClick={() => navigate({ page: "api-keys" })}
              style={styles.signOutBtn}
              data-testid="nav-api-keys"
            >
              API Keys
            </button>
          )}
          <span style={styles.operatorInfo}>
            {auth.role === "lead" ? "Lead" : "Operator"}
          </span>
          <button onClick={signOut} style={styles.signOutBtn}>
            Sign Out
          </button>
        </div>
      </header>

      {/* Page content */}
      <main style={styles.main}>
        {route.page === "dashboard" && (
          <Dashboard
            onSelect={(runId) => navigate({ page: "detail", runId })}
          />
        )}
        {route.page === "detail" && (
          <CompanyDetail
            key={route.runId}
            runId={route.runId}
            onBack={() => navigate({ page: "dashboard" })}
            onOpenRun={(runId) => navigate({ page: "detail", runId })}
          />
        )}
        {route.page === "audit" && (
          <AuditLog
            token={auth.token}
            onOpenRun={(runId) => navigate({ page: "detail", runId })}
          />
        )}
        {route.page === "api-keys" && <ApiKeys token={auth.token} />}
      </main>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

export default App;

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: "100vh",
    backgroundColor: "#f3f4f6",
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  },
  header: {
    backgroundColor: "#1e3a5f",
    color: "#ffffff",
    padding: "0.75rem 1.5rem",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  logoBtn: {
    background: "none",
    border: "none",
    color: "#ffffff",
    fontSize: "1.125rem",
    fontWeight: 700,
    cursor: "pointer",
    padding: 0,
  },
  navRight: {
    display: "flex",
    alignItems: "center",
    gap: "1rem",
  },
  operatorInfo: {
    fontSize: "0.8rem",
    color: "#93c5fd",
  },
  signOutBtn: {
    background: "none",
    border: "1px solid #93c5fd",
    color: "#93c5fd",
    padding: "0.25rem 0.75rem",
    borderRadius: "0.25rem",
    cursor: "pointer",
    fontSize: "0.8rem",
  },
  main: {
    maxWidth: "1200px",
    margin: "0 auto",
    padding: "1.5rem",
  },
};
