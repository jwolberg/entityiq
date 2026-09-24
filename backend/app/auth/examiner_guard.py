"""App-wide guard: examiners are read-only (ticket 0042; PRD-IDV C6).

Registered as a global FastAPI dependency so it covers every write route,
including routes added later, without each router remembering to check. A
Bearer token belonging to an ``examiner`` on a non-read request gets 403, and
the attempt is audited. The only writes an examiner may make are signing in
or out and replaying a screening decision (which verifies and changes
nothing).
"""

from __future__ import annotations

import re

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.operator import (
    _extract_bearer_token,
    _get_db,
    _get_operator_id_from_token,
)

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
EXAMINER_WRITE_ALLOWLIST = (
    re.compile(r"^/auth/sign-(in|out)$"),
    re.compile(r"^/screenings/[^/]+/replay$"),
)


def forbid_examiner_writes(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(_get_db),
) -> None:
    if request.method in READ_METHODS:
        return
    if any(p.match(request.url.path) for p in EXAMINER_WRITE_ALLOWLIST):
        return
    token = _extract_bearer_token(authorization)
    if token is None:
        return
    operator_id = _get_operator_id_from_token(token)
    if operator_id is None:
        return
    from app.models.operator import Operator  # noqa: PLC0415

    operator = db.get(Operator, operator_id)
    if operator is None or operator.role != "examiner":
        return
    from app.audit.recorder import record_event  # noqa: PLC0415

    record_event(
        db,
        "operator.access_denied",
        operator_id=operator.id,
        payload={
            "reason": "examiner_read_only",
            "method": request.method,
            "path": request.url.path,
        },
    )
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Examiners have read-only access.",
    )
