"""Fixtures for the officer-screening bridge API (ticket 0083)."""

import pytest

from app.main import app
from app.officer_screening import api as bridge_api
from tests.screening.conftest import api_env  # noqa: F401 — re-exported fixture


@pytest.fixture
def bridge_env(api_env):  # noqa: F811
    factory = api_env["factory"]

    def get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[bridge_api._get_db] = get_db
    yield api_env
