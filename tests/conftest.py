from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.seed import DEMO_PROPERTY_DEFINITION_ID


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "flash-test.db"))


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def demo_id() -> str:
    return DEMO_PROPERTY_DEFINITION_ID
