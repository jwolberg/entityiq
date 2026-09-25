"""Pydantic schemas for the submission endpoint.

SubmissionRequest: request body (required + optional fields per PRD § Inputs).
SubmissionResponse: 202 response body.

Network metadata (source IP, user agent, etc.) is captured server-side from the
HTTP request — it is NOT accepted from the request body.
"""

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

# Same partial-date shape the screening intake accepts (app.screening.api).
_DOB = re.compile(r"\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?")


class DeclaredPerson(BaseModel):
    """An officer or owner the submitter declares (ticket 0079).

    Each one is screened through individual screening (ticket 0081).
    """

    name: str = Field(max_length=256)
    relationship: Literal["officer", "owner"]
    role: str | None = Field(default=None, max_length=128)
    dob: str | None = None
    nationality: str | None = Field(default=None, max_length=64)
    ownership_pct: float | None = Field(default=None, ge=0, le=100)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("dob")
    @classmethod
    def _dob_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _DOB.fullmatch(v.strip()):
            raise ValueError("dob must be YYYY, YYYY-MM or YYYY-MM-DD")
        return v.strip()


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
    # Officers and owners, screened individually (tickets 0079, 0081).
    people: list[DeclaredPerson] | None = Field(default=None, max_length=50)

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
