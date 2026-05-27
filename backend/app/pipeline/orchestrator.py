"""Verification run orchestrator (P1-T2 stub — enqueue_run only).

Full orchestration (stage execution, status tracking, re-analysis) is
implemented in P1-T2.  For P1-T1 we only need enqueue_run to be importable.
"""


def enqueue_run(run_id: str) -> None:
    """Enqueue a verification run for async processing.

    Stub implementation for P1-T1.  P1-T2 replaces this with a real
    Celery task dispatch.
    """
    # No-op stub — will be replaced in P1-T2.
    pass
