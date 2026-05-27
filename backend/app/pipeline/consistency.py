"""Pipeline stage: consistency checks (P1-T6).

Placeholder — implemented in P1-T6 commit.
"""


class ConsistencyChecksStage:
    """Pipeline stage: submitted-vs-discovered consistency checks. Placeholder."""

    name = "consistency_checks"

    def run(self, run_id: str, db, context: dict) -> dict:
        return {**context, "consistency": {"status": "pending"}}
