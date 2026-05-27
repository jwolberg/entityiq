"""Pydantic schemas for the submission endpoint.

SubmissionRequest: request body (required + optional fields per PRD § Inputs).
SubmissionResponse: 202 response body.

Network metadata (source IP, user agent, etc.) is captured server-side from the
HTTP request — it is NOT accepted from the request body.
"""

from typing import Any

from pydantic import BaseModel, EmailStr, HttpUrl, model_validator


class SubmissionRequest(BaseModel):
    # --- Required fields (PRD § Inputs) ---
    company_name: str
    work_email: EmailStr
    company_domain: str
    country: str  # Will be canonicalized to ISO 3166-1 alpha-2 by normalize stage

    # --- Optional fields ---
    tax_id: str | None = None
    registration_number: str | None = None  # alias for tax_id in some locales
    billing_address: str | None = None
    phone: str | None = None
    requester_full_name: str | None = None
    linkedin_url: HttpUrl | None = None
    extra_metadata: dict[str, Any] | None = None

    # --- Client-supplied idempotency key (ARCHITECTURE § 6) ---
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def coerce_domain(self) -> "SubmissionRequest":
        """Strip scheme and trailing path from domain if provided."""
        domain = self.company_domain
        # Remove scheme if present
        for prefix in ("https://", "http://"):
            if domain.lower().startswith(prefix):
                domain = domain[len(prefix) :]
        # Strip trailing slash and any path
        domain = domain.split("/")[0].strip().lower()
        self.company_domain = domain
        return self


class SubmissionResponse(BaseModel):
    submission_id: str
    run_id: str
    status: str  # "pending"
    is_free_email_domain: bool
    message: str
