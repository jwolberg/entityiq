"""Versioned screening rules (IS2-T3, ticket 0038; PRD-IDV F13).

Weights and thresholds are data. Changing them always creates a new version.
Existing versions are never edited, so a decision's recorded rule version
still means exactly what it meant when the decision was made.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from sqlalchemy import func

from app.screening.models import ScreeningRuleVersion
from app.screening.scoring import DEFAULT_RULE

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def current_rule(db: "Session") -> ScreeningRuleVersion:
    """The highest version; creates version 1 from DEFAULT_RULE if none exist."""
    rule = (
        db.query(ScreeningRuleVersion)
        .order_by(ScreeningRuleVersion.version.desc())
        .first()
    )
    if rule is None:
        rule = ScreeningRuleVersion(
            version=1, config=copy.deepcopy(DEFAULT_RULE), note="default rule"
        )
        db.add(rule)
        db.commit()
    return rule


def new_rule_version(
    db: "Session",
    config: dict,
    *,
    note: str | None = None,
    operator_id: str | None = None,
) -> ScreeningRuleVersion:
    latest = db.query(func.max(ScreeningRuleVersion.version)).scalar() or 0
    rule = ScreeningRuleVersion(
        version=latest + 1,
        config={**copy.deepcopy(config), "version": latest + 1},
        note=note,
        created_by_operator_id=operator_id,
    )
    db.add(rule)
    db.commit()
    return rule
