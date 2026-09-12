"""Database engine, session factory and async bridge.

The application uses synchronous SQLAlchemy 2.0 with the psycopg driver.
Async call sites go through :func:`run_db`, which offloads the blocking
repository call to a worker thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings

logger = logging.getLogger(__name__)

_engine_cache: dict[str, Engine] = {}
_factory_cache: dict[str, sessionmaker] = {}


def get_engine(url: str | None = None) -> Engine:
    """Return (and cache) the SQLAlchemy engine for the configured database."""
    settings = get_settings()
    resolved = url or settings.database_url
    engine = _engine_cache.get(resolved)
    if engine is None:
        engine = create_engine(
            resolved,
            pool_pre_ping=True,
            pool_size=int(settings.db_pool_size),
            max_overflow=int(settings.db_max_overflow),
            pool_recycle=int(settings.db_pool_recycle_seconds),
            future=True,
        )
        _engine_cache[resolved] = engine
    return engine


def get_session_factory(url: str | None = None) -> sessionmaker:
    settings = get_settings()
    resolved = url or settings.database_url
    factory = _factory_cache.get(resolved)
    if factory is None:
        factory = sessionmaker(bind=get_engine(resolved), expire_on_commit=False, future=True)
        _factory_cache[resolved] = factory
    return factory


@contextmanager
def get_session(url: str | None = None) -> Iterator[Session]:
    """Yield a session inside a transaction; commit on success, rollback on error."""
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Dispose cached engines/factories (used by tests when DATABASE_URL changes)."""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()
    _factory_cache.clear()


async def run_db(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous repository call in a worker thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Raw SQL helpers (legacy '?' placeholder compatibility) ───────────────────

def _convert_placeholders(sql: str) -> str:
    """Convert positional '?' placeholders into SQLAlchemy named binds (:p0, :p1...).

    Placeholders inside single/double quoted literals are left untouched.
    """
    out: list[str] = []
    index = 0
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == "?" and not in_single and not in_double:
            out.append(f":p{index}")
            index += 1
        else:
            out.append(ch)
    return "".join(out)


def execute(session: Session, sql: str, params: tuple | list | None = None):
    """Execute a raw SQL statement with positional parameters."""
    bind = {f"p{i}": value for i, value in enumerate(params or ())}
    return session.execute(text(_convert_placeholders(sql)), bind)


def fetch_all(
    session: Session, sql: str, params: tuple | list | None = None
) -> list[dict[str, Any]]:
    return [dict(row) for row in execute(session, sql, params).mappings().all()]


def fetch_one(
    session: Session, sql: str, params: tuple | list | None = None
) -> dict[str, Any] | None:
    row = execute(session, sql, params).mappings().first()
    return dict(row) if row else None


def run_migrations() -> None:
    """Apply Alembic migrations up to head."""
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(config, "head")
