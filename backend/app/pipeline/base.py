"""Base protocol for pipeline stages.

Every stage must implement this interface.  Stages are independent: a stage
that raises is recorded as 'unavailable' and does NOT abort the run.

Stage contract:
  - name: str — unique identifier for this stage (used as the key in
    VerificationRun.source_availability).
  - run(run_id, db) — execute the stage; may read/write to the DB.
    Returns a dict of arbitrary context (passed to subsequent stages).
    Raises any exception on failure — the orchestrator catches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@runtime_checkable
class PipelineStage(Protocol):
    """Protocol all pipeline stages must satisfy."""

    @property
    def name(self) -> str:
        """Unique stage identifier (snake_case, e.g. 'normalize_input')."""
        ...

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        """Execute the stage.

        Args:
            run_id:  The VerificationRun.id this stage is operating on.
            db:      An open SQLAlchemy session (already bound to the correct
                     DB for this test or production environment).
            context: Accumulated context dict from all preceding stages.
                     Stages MAY add keys; they must NOT remove existing keys.

        Returns:
            An updated context dict (may be the same object with new keys or
            a new dict that includes all prior context).

        Raises:
            Any exception — the orchestrator records the stage as 'unavailable'
            and continues to the next stage.
        """
        ...
