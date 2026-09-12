"""Checkpointer factory — PostgreSQL-backed persistence for LangGraph state."""

from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _libpq_url() -> str:
    """Convert the SQLAlchemy URL into a libpq-compatible connection string."""
    url = get_settings().database_url
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


@lru_cache(maxsize=1)
def _get_pool():
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(conninfo=_libpq_url(), max_size=get_settings().db_pool_size, open=True)
    return pool


def get_checkpointer(*, in_memory: bool = False) -> MemorySaver:
    """Return a LangGraph checkpointer.

    Args:
        in_memory: If True, use MemorySaver (for tests). Otherwise PostgresSaver.
    """
    if in_memory:
        return MemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(_get_pool())
    saver.setup()
    return saver
