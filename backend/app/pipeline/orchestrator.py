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
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Engine
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
      3a. verify_tax_id               — Tier-1 FEIN verification (IC1-T1)
      3b. sanctions_screening         — OFAC SDN sanctions/watchlist (P2-T3)
      4. analyze_domain               — Tier-2 domain/infrastructure signals (P1-T5)
      5. enrich_network_ip            — IPinfo geo/ASN/VPN enrichment (P2-T2)
      6. web_evidence                 — Tier-3 public web evidence (P2-T4)
      7. consistency_checks           — submitted-vs-discovered comparisons (P1-T6)
      7b. geocode_hq                  — HQ coordinates + address confidence (P2-T9)
      8. scoring                      — risk assessment v1 (P1-T7)
      9. store_report                 — assemble queryable report (P1-T7)
    """
    from app.adapters.domain import AnalyzeDomainStage  # noqa: PLC0415
    from app.adapters.geocode import GeocodeHQStage  # noqa: PLC0415
    from app.adapters.ipinfo import EnrichNetworkIPStage  # noqa: PLC0415
    from app.adapters.opencorporates import QueryRegistriesStage  # noqa: PLC0415
    from app.adapters.sanctions import SanctionsScreeningStage  # noqa: PLC0415
    from app.adapters.tax_id import VerifyTaxIdStage  # noqa: PLC0415
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
        VerifyTaxIdStage(),
        SanctionsScreeningStage(),
        AnalyzeDomainStage(),
        EnrichNetworkIPStage(),
        WebEvidenceStage(),
        ConsistencyChecksStage(),
        GeocodeHQStage(),
        ScoringStage(),
        StoreReportStage(),
    ]


class StageTimeout(Exception):
    """A stage exceeded the orchestrator's per-stage time budget."""


def _run_stage_isolated(
    stage: PipelineStage,
    run_id: str,
    db: Session,
    context: dict,
    timeout_seconds: float,
) -> dict:
    """Run one stage in a worker thread on its own session, bounded in time.

    The stage gets a fresh Session inside its own transaction on the same bind
    as ``db`` (its commits become savepoints).  The transaction is committed
    only if the stage finishes within budget; a timed-out stage is abandoned
    and everything it wrote is rolled back when it eventually returns, so late
    writes never land.  Raises StageTimeout on timeout, or re-raises the
    stage's own exception.
    """
    lock = threading.Lock()
    # "settled" flips under the lock once the worker has committed or rolled
    # back, so the caller's timeout check and the worker's commit can't race.
    state: dict = {"abandoned": False, "settled": False}
    done = threading.Event()

    def worker() -> None:
        bind = db.get_bind()
        owns_conn = isinstance(bind, Engine)
        conn = bind.connect() if owns_conn else bind
        if owns_conn:
            # Production path: a plain transaction on a fresh connection.  In
            # "rollback_only" mode the stage's own commit() calls don't commit
            # it — only the orchestrator does, below — and no SAVEPOINTs are
            # needed (pysqlite mishandles them by default).
            trans = conn.begin()
            join_mode = "rollback_only"
        else:
            # Caller bound the session to a Connection with a transaction
            # already open (tests): nest so the stage's writes can be undone.
            trans = conn.begin_nested()
            join_mode = "create_savepoint"
        sess = Session(bind=conn, join_transaction_mode=join_mode)
        try:
            result = stage.run(run_id, sess, dict(context))
            # Close out the session's own transaction first; ``trans`` still
            # decides whether any of it is kept.
            sess.commit()
            with lock:
                if state["abandoned"]:
                    if trans.is_active:
                        trans.rollback()
                else:
                    if trans.is_active:
                        trans.commit()
                    state["result"] = result
                state["settled"] = True
        except BaseException as exc:  # noqa: BLE001 — surfaced to the caller
            sess.rollback()
            with lock:
                if trans.is_active:
                    trans.rollback()
                state["error"] = exc
                state["settled"] = True
        finally:
            sess.close()
            if owns_conn:
                conn.close()
            done.set()

    thread = threading.Thread(
        target=worker, name=f"stage-{stage.name}-{run_id}", daemon=True
    )
    thread.start()
    try:
        done.wait(timeout_seconds)
    except BaseException:
        # Interrupted while waiting (e.g. Celery's soft time limit): abandon
        # the stage so nothing it writes afterwards lands, then propagate.
        with lock:
            if not state["settled"]:
                state["abandoned"] = True
        raise
    with lock:
        if not state["settled"]:
            state["abandoned"] = True
            raise StageTimeout(
                f"stage {stage.name!r} exceeded {timeout_seconds:.1f}s budget"
            )
    if "error" in state:
        raise state["error"]
    # The stage committed on another session; drop our stale identity map.
    db.expire_all()
    return state["result"]


class Orchestrator:
    """Drives a VerificationRun through an ordered list of pipeline stages.

    Args:
        stages: Ordered list of PipelineStage instances.  Stages execute in
                order; a failing stage is skipped (unavailable) but the run
                continues.
        stage_timeout_seconds: Per-stage time budget.  When set, each stage
                runs in a worker thread on its own DB session and is recorded
                as unavailable (its writes discarded) if it overruns.  None
                keeps the original in-thread, shared-session behavior.
        run_timeout_seconds: Whole-run budget.  Once spent, remaining stages
                are skipped as unavailable, except those marked
                ``always_run`` (scoring, store_report), so the report is still
                produced.  Also caps each stage's timeout at the budget left.
    """

    def __init__(
        self,
        stages: list[PipelineStage],
        stage_timeout_seconds: float | None = None,
        run_timeout_seconds: float | None = None,
    ) -> None:
        self.stages = stages
        self.stage_timeout_seconds = stage_timeout_seconds
        self.run_timeout_seconds = run_timeout_seconds

    def _stage_budget(
        self, stage: PipelineStage, deadline: float | None
    ) -> float | None:
        budget = self.stage_timeout_seconds
        if deadline is not None and not getattr(stage, "always_run", False):
            remaining = max(0.0, deadline - time.monotonic())
            budget = remaining if budget is None else min(budget, remaining)
        return budget

    def _run_stage(
        self,
        stage: PipelineStage,
        run_id: str,
        db: Session,
        context: dict,
        budget: float | None,
    ) -> dict:
        if budget is None:
            return stage.run(run_id, db, context)
        return _run_stage_isolated(stage, run_id, db, context, budget)

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
        deadline = (
            time.monotonic() + self.run_timeout_seconds
            if self.run_timeout_seconds is not None
            else None
        )

        try:
            for stage in self.stages:
                stage_name = stage.name
                if (
                    deadline is not None
                    and time.monotonic() >= deadline
                    and not getattr(stage, "always_run", False)
                ):
                    logger.warning(
                        "Orchestrator: run %s budget spent; skipping stage %s",
                        run_id,
                        stage_name,
                    )
                    _update_stage_status(run, db, stage_name, STAGE_UNAVAILABLE)
                    continue
                logger.info(
                    "Orchestrator: run %s starting stage %s", run_id, stage_name
                )
                try:
                    before = context
                    context = self._run_stage(
                        stage, run_id, db, context, self._stage_budget(stage, deadline)
                    )
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
                except SoftTimeLimitExceeded:
                    raise
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

        except SoftTimeLimitExceeded:
            # Celery's backstop fired before the run budget could wrap up.
            # Keep what we have: mark unfinished stages unavailable, fail the
            # run with a clear reason, and still assemble the partial report.
            logger.error("Orchestrator: run %s hit the task time limit", run_id)
            _finish_after_time_limit(run_id, db)
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


def _finish_after_time_limit(run_id: str, db: Session) -> None:
    from app.scoring.report import assemble_report  # noqa: PLC0415

    try:
        db.rollback()
        run = db.get(VerificationRun, run_id)
        if run is None:
            return
        run.source_availability = {
            name: (STAGE_UNAVAILABLE if status == STAGE_PENDING else status)
            for name, status in (run.source_availability or {}).items()
        }
        run.status = "failed"
        run.finished_at = datetime.now(tz=timezone.utc)
        run.failure_reason = "Task time limit exceeded; partial report kept."
        db.commit()
        assemble_report(run_id, db)
        db.commit()
    except Exception:
        logger.exception("Orchestrator: run %s partial-report save failed", run_id)


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
