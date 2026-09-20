import os

os.environ.setdefault("IGPOS_DATABASE_URL", "postgresql+psycopg://igpos:igpos_dev_local@localhost:5432/igpos_test")

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import engine
import app.domain  # noqa: F401 populate metadata


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Session:
    """
    Each test runs inside an outer transaction + savepoint. A `db.commit()`
    inside the code under test (e.g. app/services/checkout.py genuinely
    calls db.commit()) only commits the *savepoint*, not the outer
    transaction — SQLAlchemy 2.0's join_transaction_mode="create_savepoint"
    handles re-opening a fresh savepoint after each commit. The final
    rollback undoes everything, so tests never leak data (or unique-
    constraint collisions like duplicate seeded emails) into each other,
    even though the code being tested calls real commit().
    """
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
