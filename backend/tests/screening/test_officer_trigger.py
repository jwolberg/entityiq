"""kyb_officer screening trigger (ticket 0078)."""

from app.screening.models import RUN_TRIGGERS


def test_kyb_officer_is_a_run_trigger():
    assert "kyb_officer" in RUN_TRIGGERS
