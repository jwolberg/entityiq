/**
 * NewCompanyForm — modal form for submitting a new company to verify.
 *
 * Opened from the Verification Queue ("Check New Company" button).  On submit
 * it POSTs to /submissions, which runs the verification pipeline (inline in
 * eager mode) and produces a report.  On success the parent closes the modal
 * and refreshes the queue so the new row appears.
 */

import { FormEvent, useState } from "react";
import { apiClient, SubmissionRequest } from "../api/client";

interface NewCompanyFormProps {
  token: string;
  onClose: () => void;
  onSuccess: () => void;
}

interface FieldState {
  company_name: string;
  work_email: string;
  company_domain: string;
  country: string;
  tax_id: string;
  billing_address: string;
  phone: string;
  requester_full_name: string;
  linkedin_url: string;
}

const EMPTY: FieldState = {
  company_name: "",
  work_email: "",
  company_domain: "",
  country: "",
  tax_id: "",
  billing_address: "",
  phone: "",
  requester_full_name: "",
  linkedin_url: "",
};

export function NewCompanyForm({
  token,
  onClose,
  onSuccess,
}: NewCompanyFormProps) {
  const [fields, setFields] = useState<FieldState>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(key: keyof FieldState, value: string) {
    setFields((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    // Build the request, omitting empty optional fields. linkedin_url in
    // particular is validated as a URL server-side, so an empty string would
    // 422 — send it only when present.
    const body: SubmissionRequest = {
      company_name: fields.company_name.trim(),
      work_email: fields.work_email.trim(),
      company_domain: fields.company_domain.trim(),
      country: fields.country.trim(),
    };
    const optional: (keyof SubmissionRequest)[] = [
      "tax_id",
      "billing_address",
      "phone",
      "requester_full_name",
      "linkedin_url",
    ];
    for (const key of optional) {
      const v = fields[key].trim();
      if (v) {
        body[key] = v;
      }
    }

    setSubmitting(true);
    try {
      await apiClient.submitCompany(body, token);
      onSuccess();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Submission failed. Try again.",
      );
      setSubmitting(false);
    }
  }

  return (
    <div
      style={styles.backdrop}
      onClick={onClose}
      data-testid="new-company-backdrop"
    >
      <div
        style={styles.modal}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Check a new company"
        data-testid="new-company-form"
      >
        <div style={styles.modalHeader}>
          <h2 style={styles.title}>Check New Company</h2>
          <button
            type="button"
            onClick={onClose}
            style={styles.closeBtn}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p style={styles.subtitle}>
          Enter what you know. We'll collect everything we can find and produce a
          risk report.
        </p>

        <form onSubmit={handleSubmit}>
          <div style={styles.sectionLabel}>Required</div>
          <Field
            label="Company name"
            value={fields.company_name}
            onChange={(v) => update("company_name", v)}
            required
            testid="field-company_name"
          />
          <Field
            label="Work email"
            type="email"
            value={fields.work_email}
            onChange={(v) => update("work_email", v)}
            required
            testid="field-work_email"
          />
          <Field
            label="Company domain"
            value={fields.company_domain}
            onChange={(v) => update("company_domain", v)}
            placeholder="example.com"
            required
            testid="field-company_domain"
          />
          <Field
            label="Country"
            value={fields.country}
            onChange={(v) => update("country", v)}
            placeholder="US"
            required
            testid="field-country"
          />

          <div style={styles.sectionLabel}>Optional</div>
          <Field
            label="Tax ID / registration number"
            value={fields.tax_id}
            onChange={(v) => update("tax_id", v)}
            testid="field-tax_id"
          />
          <Field
            label="Billing address"
            value={fields.billing_address}
            onChange={(v) => update("billing_address", v)}
            testid="field-billing_address"
          />
          <Field
            label="Phone"
            value={fields.phone}
            onChange={(v) => update("phone", v)}
            testid="field-phone"
          />
          <Field
            label="Requester full name"
            value={fields.requester_full_name}
            onChange={(v) => update("requester_full_name", v)}
            testid="field-requester_full_name"
          />
          <Field
            label="LinkedIn URL"
            type="url"
            value={fields.linkedin_url}
            onChange={(v) => update("linkedin_url", v)}
            placeholder="https://www.linkedin.com/company/..."
            testid="field-linkedin_url"
          />

          {error && (
            <div style={styles.error} role="alert" data-testid="new-company-error">
              {error}
            </div>
          )}

          <div style={styles.actions}>
            <button
              type="button"
              onClick={onClose}
              style={styles.cancelBtn}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              style={{
                ...styles.submitBtn,
                ...(submitting ? styles.submitBtnDisabled : {}),
              }}
              disabled={submitting}
              data-testid="submit-new-company"
            >
              {submitting ? "Collecting data…" : "Run check"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
  testid?: string;
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required,
  testid,
}: FieldProps) {
  return (
    <label style={styles.field}>
      <span style={styles.label}>
        {label}
        {required && <span style={styles.req}> *</span>}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        style={styles.input}
        data-testid={testid}
      />
    </label>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(17, 24, 39, 0.5)",
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "center",
    padding: "2rem 1rem",
    overflowY: "auto",
    zIndex: 50,
  },
  modal: {
    backgroundColor: "#ffffff",
    borderRadius: "0.5rem",
    boxShadow: "0 10px 25px rgba(0,0,0,0.2)",
    width: "100%",
    maxWidth: "520px",
    padding: "1.5rem",
  },
  modalHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  title: {
    margin: 0,
    fontSize: "1.25rem",
    fontWeight: 700,
    color: "#111827",
  },
  closeBtn: {
    background: "none",
    border: "none",
    fontSize: "1.5rem",
    lineHeight: 1,
    cursor: "pointer",
    color: "#6b7280",
    padding: "0 0.25rem",
  },
  subtitle: {
    margin: "0.25rem 0 1rem",
    color: "#6b7280",
    fontSize: "0.875rem",
  },
  sectionLabel: {
    fontSize: "0.75rem",
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    color: "#9ca3af",
    margin: "1rem 0 0.5rem",
  },
  field: {
    display: "block",
    marginBottom: "0.75rem",
  },
  label: {
    display: "block",
    fontSize: "0.8125rem",
    fontWeight: 600,
    color: "#374151",
    marginBottom: "0.25rem",
  },
  req: {
    color: "#dc2626",
  },
  input: {
    width: "100%",
    padding: "0.5rem 0.625rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    boxSizing: "border-box",
  },
  error: {
    marginTop: "0.75rem",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: "0.375rem",
    color: "#b91c1c",
    fontSize: "0.8125rem",
  },
  actions: {
    display: "flex",
    justifyContent: "flex-end",
    gap: "0.5rem",
    marginTop: "1.25rem",
  },
  cancelBtn: {
    padding: "0.5rem 1rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    backgroundColor: "#ffffff",
    color: "#374151",
    fontWeight: 600,
    fontSize: "0.875rem",
    cursor: "pointer",
  },
  submitBtn: {
    padding: "0.5rem 1rem",
    border: "none",
    borderRadius: "0.375rem",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    fontWeight: 600,
    fontSize: "0.875rem",
    cursor: "pointer",
  },
  submitBtnDisabled: {
    backgroundColor: "#93c5fd",
    cursor: "not-allowed",
  },
};
