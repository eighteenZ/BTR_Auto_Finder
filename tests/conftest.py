"""Shared pytest fixtures.

Database strategy:
  * If ``DATABASE_URL`` is set, tests use that PostgreSQL database.
  * Otherwise a disposable PostgreSQL container is started via testcontainers.
  * The schema is created once per session and truncated between tests.
"""

from __future__ import annotations

import os

import pytest

_DB_URL = os.environ.get("DATABASE_URL", "").strip()
_container = None

if not _DB_URL:
    try:
        from testcontainers.postgres import PostgresContainer

        _container = PostgresContainer("postgres:16-alpine")
        _container.start()
        _DB_URL = _container.get_connection_url()
        _DB_URL = _DB_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    except Exception as exc:  # noqa: BLE001
        print(f"[conftest] PostgreSQL unavailable; database-backed tests will fail: {exc}")
        _DB_URL = ""

if _DB_URL:
    os.environ["DATABASE_URL"] = _DB_URL

from config.settings import get_settings  # noqa: E402

get_settings.cache_clear()


def pytest_configure(config):
    config.addinivalue_line("markers", "db: marks tests that require PostgreSQL")


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    if _DB_URL:
        from persistence.db import get_engine
        from persistence.models import Base

        Base.metadata.create_all(get_engine())
    else:
        print("[conftest] no database; skipping schema creation")
    yield
    if _container is not None:
        try:
            _container.stop()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(autouse=True)
def _clean_database():
    if not _DB_URL:
        yield
        return
    from persistence.db import get_engine
    from persistence.models import Base

    engine = get_engine()
    tables = ", ".join(
        table.name for table in reversed(Base.metadata.sorted_tables)
    )
    if tables:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    yield
