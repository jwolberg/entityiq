"""Verification run orchestrator.

Drives a VerificationRun through its registered pipeline stages, recording
per-stage status, writing partial results, and handling stage failures
gracefully (a failing stage is recorded as 'unavailable' — it does NOT abort
the run).

Usage patterns:

  1. Synchronous (tests, CLI):
       from app.pipeline.orchestrator import Orchestrator, default_stages
       orch = Orchestrator(default_stages())
       orch.run_sync(run_id, db)

  2. Via Celery (production):
       from app.pipeline.orchestrator import enqueue_run
       enqueue_run(run_id)
       # The Celery worker calls run_verification_task, which calls run_sync
       # with its own DB session.

Testability: run_sync() takes an explicit db session, so tests can pass an
in-memory SQLite session directly.  Celery workers can be configured with
task_always_eager=True for integration tests that still need the Celery path.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.verification_run import VerificationRun
from app.pipeline.base import PipelineStage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Status constants for source_availability entries.
STAGE_COMPLETE = "complete"
STAGE_UNAVAILABLE = "unavailable"
STAGE_PENDING = "pending"

# AdapterFailure kinds that mean the SOURCE was down (vs. "not_found", which is
# a legitimate finding).  Adapter stages never raise on these — they return a
# context entry with this status — so the orchestrator must look for them or the
# outage is recorded as "complete" and silently vanishes from the report.
_OUTAGE_KINDS = frozenset({"timeout", "unavailable", "rate_limited"})


def _stage_reported_outage(before: dict, after: dict) -> bool:
    """True if the stage added/replaced a context entry with an outage status."""
    for key, value in after.items():
        if value is before.get(key):
            continue
        if isinstance(value, dict) and value.get("status") in _OUTAGE_KINDS:
            return True
    return False


def default_stages() -> list[PipelineStage]:
    """Return the ordered list of registered pipeline stages.

    Stages are imported lazily here so that:
      a) the orchestrator doesn't need to import every stage at module load, and
      b) tests can register their own stages without touching this function.

    Current order (ARCHITECTURE § 2):
      1. normalize_input              — canonicalize submitted fields (P1-T3)
      2. resolve_entity_candidates    — rank candidate entities (P2-T1)
      3. query_registries             — Tier-1 authoritative lookup (P1-T4)
      3b. sanctions_screening         — OFAC SDN sanctions/watchlist (P2-T3)
      4. analyze_domain               — Tier-2 domain/infrastructure signals (P1-T5)
      5. enrich_network_ip            — IPinfo geo/ASN/VPN enrichment (P2-T2)
      6. web_evidence                 — Tier-3 public web evidence (P2-T4)
      7. consistency_checks           — submitted-vs-discovered comparisons (P1-T6)
      8. scoring                      — risk assessment v1 (P1-T7)
      9. store_report                 — assemble queryable report (P1-T7)
    """
    from app.adapters.domain import AnalyzeDomainStage  # noqa: PLC0415
    from app.adapters.ipinfo import EnrichNetworkIPStage  # noqa: PLC0415
    from app.adapters.opencorporates import QueryRegistriesStage  # noqa: PLC0415
    from app.adapters.sanctions import SanctionsScreeningStage  # noqa: PLC0415
    from app.adapters.web import WebEvidenceStage  # noqa: PLC0415
    from app.pipeline.consistency import ConsistencyChecksStage  # noqa: PLC0415
    from app.pipeline.normalize import NormalizeInputStage  # noqa: PLC0415
    from app.pipeline.resolve import ResolveEntityCandidatesStage  # noqa: PLC0415
    from app.scoring.engine import ScoringStage  # noqa: PLC0415
    from app.scoring.report import StoreReportStage  # noqa: PLC0415

    return [
        NormalizeInputStage(),
        ResolveEntityCandidatesStage(),
        QueryRegistriesStage(),
        SanctionsScreeningStage(),
        AnalyzeDomainStage(),
        EnrichNetworkIPStage(),
        WebEvidenceStage(),
        ConsistencyChecksStage(),
        ScoringStage(),
        StoreReportStage(),
    ]


class Orchestrator:
    """Drives a VerificationRun through an ordered list of pipeline stages.

    Args:
        stages: Ordered list of PipelineStage instances.  Stages execute in
                order; a failing stage is skipped (unavailable) but the run
                continues.
    """

    def __init__(self, stages: list[PipelineStage]) -> None:
        self.stages = stages

    # ------------------------------------------------------------------
    # Public: synchronous execution (used directly in tests and by the
    # Celery task).
    # ------------------------------------------------------------------

    def run_sync(self, run_id: str, db: Session) -> None:
        """Drive the run synchronously using the provided DB session.

        Transitions:
          pending → running → complete (or failed if the pre/post-amble raises)

        Per-stage status is written to VerificationRun.source_availability
        after each stage completes, making partial results readable mid-run.
        """
        run = db.get(VerificationRun, run_id)
        if run is None:
            logger.error("Orchestrator: run %s not found", run_id)
            return

        # Transition: pending → running
        run.status = "running"
        run.started_at = datetime.now(tz=timezone.utc)
        run.source_availability = {stage.name: STAGE_PENDING for stage in self.stages}
        db.commit()

        context: dict = {}

        try:
            for stage in self.stages:
                stage_name = stage.name
                logger.info(
                    "Orchestrator: run %s starting stage %s", run_id, stage_name
                )
                try:
                    before = context
                    context = stage.run(run_id, db, context)
                    # Persist partial result visibility after each stage.
                    status = (
                        STAGE_UNAVAILABLE
                        if _stage_reported_outage(before, context)
                        else STAGE_COMPLETE
                    )
                    _update_stage_status(run, db, stage_name, status)
                    logger.info(
                        "Orchestrator: run %s stage %s complete",
                        run_id,
                        stage_name,
                    )
                except Exception:
                    # A failing stage is recorded as unavailable; the run continues.
                    tb = traceback.format_exc()
                    logger.warning(
                        "Orchestrator: run %s stage %s unavailable:\n%s",
                        run_id,
                        stage_name,
                        tb,
                    )
                    _update_stage_status(run, db, stage_name, STAGE_UNAVAILABLE)

            # All stages attempted — transition to complete.
            run.status = "complete"
            run.finished_at = datetime.now(tz=timezone.utc)
            db.commit()
            logger.info("Orchestrator: run %s complete", run_id)

        except Exception:
            # Unexpected error outside stage execution (e.g. DB failure).
            tb = traceback.format_exc()
            logger.error("Orchestrator: run %s failed unexpectedly:\n%s", run_id, tb)
            try:
                run = db.get(VerificationRun, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(tz=timezone.utc)
                    run.failure_reason = tb[:2000]
                    db.commit()
            except Exception:
                pass


def _update_stage_status(
    run: VerificationRun, db: Session, stage_name: str, status: str
) -> None:
    """Write per-stage status to source_availability and flush to DB.

    We use a copy-assign to ensure SQLAlchemy detects the mutation to the
    JSON column (in-place dict mutation is not always tracked).
    """
    current = dict(run.source_availability or {})
    current[stage_name] = status
    run.source_availability = current
    db.commit()


# ------------------------------------------------------------------
# Celery integration
# ------------------------------------------------------------------


def enqueue_run(run_id: str) -> None:
    """Enqueue a verification run for async processing via Celery.

    In production this dispatches to the Celery worker.
    In tests with task_always_eager=True the task runs synchronously
    in-process (no broker needed).
    """
    from app.worker import run_verification_task  # noqa: PLC0415

    run_verification_task.delay(run_id)


# ------------------------------------------------------------------
# Re-analysis helper
# ------------------------------------------------------------------


def enqueue_reanalysis(entity_id: str, supersedes_run_id: str, db: Session) -> str:
    """Create a new VerificationRun superseding the given run and enqueue it.

    Args:
        entity_id:         The entity to re-analyse.
        supersedes_run_id: The run_id of the run being superseded.
        db:                Open session to persist the new run.

    Returns:
        The new run_id.
    """
    # Find the submission linked to the superseded run.
    prior_run = db.get(VerificationRun, supersedes_run_id)
    if prior_run is None:
        raise ValueError(f"Run {supersedes_run_id!r} not found")

    new_run = VerificationRun(
        submission_id=prior_run.submission_id,
        entity_id=entity_id,
        status="pending",
        supersedes_id=supersedes_run_id,
    )
    db.add(new_run)
    db.commit()

    enqueue_run(new_run.id)
    return new_run.id
