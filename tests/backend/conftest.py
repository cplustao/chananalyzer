from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_MODE"] = "local"
os.environ["DATABASE_URL"] = "sqlite:///data/chan_v2_test.db"
TEST_DB = Path(__file__).resolve().parents[2] / "data" / "chan_v2_test.db"
for candidate in (TEST_DB, TEST_DB.with_suffix(".db-shm"), TEST_DB.with_suffix(".db-wal")):
    candidate.unlink(missing_ok=True)
from backend.app.db.base import Base
from backend.app.db.session import SessionLocal, engine
from backend.app.main import app


@pytest.fixture(scope="session", autouse=True)
def database_schema():
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    for candidate in (TEST_DB, TEST_DB.with_suffix(".db-shm"), TEST_DB.with_suffix(".db-wal")):
        try:
            candidate.unlink(missing_ok=True)
        except PermissionError:
            pass
@pytest.fixture(autouse=True)
def clean_database(database_schema):
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield


@pytest.fixture()
def session():
    with SessionLocal() as value:
        yield value
@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    with TestClient(app) as value:
        yield value