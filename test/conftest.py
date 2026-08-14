import os

os.environ.setdefault("INVOICING_DATABASE_URL", "sqlite:///./invoicing_test.db")
os.environ.setdefault("INVOICING_STORAGE_BACKEND", "local")
os.environ.setdefault("INVOICING_STORAGE_ROOT", "./data/test-originals")
os.environ.setdefault("INVOICING_QUEUE_BACKEND", "local")
os.environ.setdefault("INVOICING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("INVOICING_JWT_SECRET", "test-secret")
os.environ.setdefault("INVOICING_ADMIN_PASSWORD", "admin123")

import pytest
from sqlalchemy import create_engine

from invoicing.config import settings
from invoicing.db import Base, SessionLocal


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()
