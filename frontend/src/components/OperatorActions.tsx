/**
 * OperatorActions — full operator workbench actions for a run (P2-T10).
 *
 * Wraps the P2-T11 backend endpoints:
 *   - Re-run analysis        → POST /reanalysis/{run_id}
 *   - Correct data + re-run  → POST /workflow/runs/{run_id}/correct
 *   - Add notes              → POST /workflow/runs/{run_id}/notes
 *   - Export report (JSON)   → GET  /reports/{run_id}/export
 *
 * Correct-and-re-run and re-run both produce a NEW run that supersedes the
 * current one; on success we offer to open it via `onOpenRun`.  All four
 * actions are operator-audited server-side.
 */

import { useState } from "react";
import {
  AddNotesResponse,
  apiClient,
  CorrectableField,
  CorrectAndRerunRequest,
} from "../api/client";

interface OperatorActionsProps {
  runId: string;
  token: string;
  /** Submitted values keyed by correctable field, for prefilling the form. */
  submittedValues: Partial<Record<CorrectableField, string>>;
  /** Navigate to a (new) run — used after re-run / correct-and-re-run. */
  onOpenRun?: (runId: string) => void;
  /** Called after notes are saved (saving notes also records a review). */
  onNotesSaved?: (resp: AddNotesResponse) => void;
}

// Correctable fields in display order (must be a subset of CorrectableField).
const CORRECTABLE_FIELDS: { key: CorrectableField; label: string }[] = [
  { key: "company_name", label: "Company Name" },
  { key: "domain", label: "Domain" },
  { key: "work_email", label: "Work Email" },
  { key: "country", label: "Country" },
  { key: "tax_id", label: "Tax ID" },
  { key: "billing_address", label: "Billing Address" },
  { key: "phone", label: "Phone" },
  { key: "requester_full_name", label: "Requester Name" },
  { key: "linkedin_url", label: "LinkedIn URL" },
];

export function OperatorActions({
  runId,
  token,
  submittedValues,
  onOpenRun,
  onNotesSaved,
}: OperatorActionsProps) {
  // --- Re-run analysis ---
  const [rerunning, setRerunning] = useState(false);
  const [newRunId, setNewRunId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // --- Correct + re-run form ---
  const initialValues = (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const f of CORRECTABLE_FIELDS) {
      out[f.key] = submittedValues[f.key] ?? "";
    }
    return out;
  };
  const [showCorrect, setShowCorrect] = useState(false);
  const [fields, setFields] = useState<Record<string, string>>(initialValues);
  const [correcting, setCorrecting] = useState(false);

  // --- Add notes ---
  const [notes, setNotes] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesSaved, setNotesSaved] = useState(false);

  // --- Export ---
  const [exporting, setExporting] = useState(false);

  const base = initialValues();
  const changedCorrections = (): CorrectAndRerunRequest["corrections"] => {
    const corrections: CorrectAndRerunRequest["corrections"] = {};
    for (const f of CORRECTABLE_FIELDS) {
      const next = fields[f.key].trim();
      if (next !== (base[f.key] ?? "").trim()) {
        corrections[f.key] = next === "" ? null : next;
      }
    }
    return corrections;
  };

  async function handleRerun() {
    setRerunning(true);
    setActionError(null);
    try {
      const resp = await apiClient.triggerReanalysis(runId, token);
      setNewRunId(resp.new_run_id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Re-run failed");
    } finally {
      setRerunning(false);
    }
  }

  async function handleCorrect() {
    const corrections = changedCorrections();
    if (Object.keys(corrections).length === 0) {
      setActionError("Change at least one field before re-running.");
      return;
    }
    setCorrecting(true);
    setActionError(null);
    try {
      const resp = await apiClient.correctAndRerun(
        runId,
        { corrections },
        token
      );
      setNewRunId(resp.new_run_id);
      setShowCorrect(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Correction failed");
    } finally {
      setCorrecting(false);
    }
  }

  async function handleAddNotes() {
    if (!notes.trim()) return;
    setSavingNotes(true);
    setActionError(null);
    try {
      const resp = await apiClient.addNotes(runId, { notes: notes.trim() }, token);
      onNotesSaved?.(resp);
      setNotesSaved(true);
      setNotes("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Saving notes failed");
    } finally {
      setSavingNotes(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    setActionError(null);
    try {
      const report = await apiClient.exportReport(runId, token);
      const json = JSON.stringify(report, null, 2);
      // Trigger a browser download when the environment supports it.
      if (typeof URL !== "undefined" && URL.createObjectURL) {
        const blob = new Blob([json], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `entityiq-report-${runId}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div data-testid="operator-actions">
      {/* Primary action buttons */}
      <div style={styles.btnRow}>
        <button
          onClick={handleRerun}
          disabled={rerunning}
          style={styles.btn}
          data-testid="rerun-btn"
        >
          {rerunning ? "Re-running…" : "Re-run Analysis"}
        </button>
        <button
          onClick={() => setShowCorrect((s) => !s)}
          style={styles.btnSecondary}
          data-testid="toggle-correct-btn"
        >
          {showCorrect ? "Cancel Correction" : "Correct Data & Re-run"}
        </button>
        <button
          onClick={handleExport}
          disabled={exporting}
          style={styles.btnSecondary}
          data-testid="export-btn"
        >
          {exporting ? "Exporting…" : "Export Report (JSON)"}
        </button>
      </div>

      {actionError && (
        <p style={styles.error} role="alert" data-testid="action-error">
          {actionError}
        </p>
      )}

      {newRunId && (
        <div style={styles.successBanner} data-testid="new-run-banner">
          New run <code>{newRunId}</code> enqueued — it supersedes this run.{" "}
          {onOpenRun && (
            <button
              onClick={() => onOpenRun(newRunId)}
              style={styles.linkBtn}
              data-testid="open-new-run-btn"
            >
              View new run →
            </button>
          )}
        </div>
      )}

      {/* Correction form */}
      {showCorrect && (
        <div style={styles.correctForm} data-testid="correct-form">
          <p style={styles.formNote}>
            Edit only the fields you want to correct. A new run will supersede
            this one.
          </p>
          {CORRECTABLE_FIELDS.map((f) => (
            <div key={f.key} style={styles.field}>
              <label htmlFor={`correct-${f.key}`} style={styles.fieldLabel}>
                {f.label}
              </label>
              <input
                id={`correct-${f.key}`}
                type="text"
                value={fields[f.key]}
                onChange={(e) =>
                  setFields((prev) => ({ ...prev, [f.key]: e.target.value }))
                }
                disabled={correcting}
                style={styles.input}
                data-testid={`correct-input-${f.key}`}
              />
            </div>
          ))}
          <button
            onClick={handleCorrect}
            disabled={correcting}
            style={styles.btn}
            data-testid="submit-correct-btn"
          >
            {correcting ? "Submitting…" : "Apply Corrections & Re-run"}
          </button>
        </div>
      )}

      {/* Add notes */}
      <div style={styles.notesBlock}>
        <label htmlFor="operator-note" style={styles.fieldLabel}>
          Add a note
        </label>
        <textarea
          id="operator-note"
          value={notes}
          onChange={(e) => {
            setNotes(e.target.value);
            setNotesSaved(false);
          }}
          rows={2}
          disabled={savingNotes}
          placeholder="Record context, a follow-up, or a decision rationale…"
          style={styles.textarea}
          data-testid="note-input"
        />
        <button
          onClick={handleAddNotes}
          disabled={savingNotes || !notes.trim()}
          style={styles.btnSecondary}
          data-testid="add-note-btn"
        >
          {savingNotes ? "Saving…" : "Add Note"}
        </button>
        {notesSaved && (
          <span style={styles.savedHint} data-testid="note-saved">
            Note saved.
          </span>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  btnRow: {
    display: "flex",
    flexWrap: "wrap",
    gap: "0.5rem",
    marginBottom: "0.5rem",
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
    padding: "0.5rem 1rem",
    backgroundColor: "#ffffff",
    color: "#2563eb",
    border: "1px solid #2563eb",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  linkBtn: {
    background: "none",
    border: "none",
    color: "#1d4ed8",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: "0.85rem",
    padding: 0,
  },
  error: {
    color: "#dc2626",
    fontSize: "0.85rem",
    margin: "0.5rem 0 0",
  },
  successBanner: {
    marginTop: "0.75rem",
    padding: "0.75rem 1rem",
    backgroundColor: "#f0fdf4",
    border: "1px solid #86efac",
    borderRadius: "0.375rem",
    color: "#166534",
    fontSize: "0.85rem",
  },
  correctForm: {
    marginTop: "0.75rem",
    padding: "1rem",
    backgroundColor: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: "0.375rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  formNote: {
    margin: "0 0 0.25rem",
    fontSize: "0.8rem",
    color: "#6b7280",
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: "0.2rem",
  },
  fieldLabel: {
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#374151",
  },
  input: {
    padding: "0.4rem 0.6rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
  },
  notesBlock: {
    marginTop: "1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.4rem",
    alignItems: "flex-start",
  },
  textarea: {
    width: "100%",
    boxSizing: "border-box",
    padding: "0.5rem 0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    fontFamily: "inherit",
    resize: "vertical",
  },
  savedHint: {
    fontSize: "0.8rem",
    color: "#16a34a",
  },
};
