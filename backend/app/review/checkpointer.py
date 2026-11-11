"""Where paused reviews are stored.

A review pauses until the therapist answers, which can be minutes or days later,
and the API process may restart in between. Postgres keeps the paused state.
Tests and the SQLite fallback use an in memory saver instead.
"""

from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ..config import get_settings


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


@lru_cache
def get_checkpointer() -> BaseCheckpointSaver:
    url = get_settings().database_url
    if not _is_postgres(url):
        return InMemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # SQLAlchemy URLs name the driver ("postgresql+psycopg://"). psycopg does not.
    conninfo = url.replace("postgresql+psycopg://", "postgresql://", 1)
    pool = ConnectionPool(
        conninfo,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()  # creates the checkpoint tables on first use
    return saver


def close_checkpointer() -> None:
    """Close the connection pool on shutdown, if one was opened."""
    if get_checkpointer.cache_info().currsize == 0:
        return
    pool = getattr(get_checkpointer(), "conn", None)  # only the Postgres saver has one
    if pool is not None and hasattr(pool, "close"):
        pool.close()
    get_checkpointer.cache_clear()
