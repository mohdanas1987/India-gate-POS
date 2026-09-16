import os

os.environ.setdefault("IGPOS_DATABASE_URL", "postgresql+psycopg://igpos:igpos_dev_local@localhost:5432/igpos_test")

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import engine, SessionLocal
import app.domain  # noqa: F401 populate metadata


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
