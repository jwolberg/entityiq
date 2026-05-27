"""Tier-2 domain/infrastructure signals adapter (P1-T5).

Placeholder — implemented in P1-T5 commit.
"""


class AnalyzeDomainStage:
    """Pipeline stage: analyze domain infrastructure (stage 4). Placeholder."""

    name = "analyze_domain"

    def run(self, run_id: str, db, context: dict) -> dict:
        return {**context, "domain_signals": {"status": "pending"}}
