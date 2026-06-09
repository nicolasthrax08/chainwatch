"""
ChainWatch Instrumented DB Pool Wrapper
========================================
Wraps asyncpg pool/connection to record query metrics.
Transparently passes through all operations while counting
queries and errors for the health_metrics service.

Usage:
    from services.instrumented_pool import instrument_pool
    instrument_pool(db_pool)  # call after pool creation in startup
"""
import logging
from typing import Optional

from services.health_metrics import record_db_query

logger = logging.getLogger("chainwatch.db_instrumentation")


def instrument_pool(pool) -> None:
    """
    Instrument an asyncpg pool to record query metrics.
    Patches the pool's acquire() method to return instrumented connections.
    """
    if pool is None:
        return

    original_acquire = pool.acquire

    async def instrumented_acquire(*args, **kwargs):
        conn = await original_acquire(*args, **kwargs)
        return _InstrumentedConnection(conn)

    pool.acquire = instrumented_acquire
    logger.debug("DB pool instrumented for query metrics")


class _InstrumentedConnection:
    """
    Wraps an asyncpg connection to record query metrics.
    Delegates all attribute access to the underlying connection.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def __aenter__(self):
        return await self._conn.__aenter__()

    async def __aexit__(self, *args):
        return await self._conn.__aexit__(*args)

    async def fetch(self, query, *args, **kwargs):
        try:
            result = await self._conn.fetch(query, *args, **kwargs)
            record_db_query(success=True)
            return result
        except Exception:
            record_db_query(success=False)
            raise

    async def fetchrow(self, query, *args, **kwargs):
        try:
            result = await self._conn.fetchrow(query, *args, **kwargs)
            record_db_query(success=True)
            return result
        except Exception:
            record_db_query(success=False)
            raise

    async def fetchval(self, query, *args, **kwargs):
        try:
            result = await self._conn.fetchval(query, *args, **kwargs)
            record_db_query(success=True)
            return result
        except Exception:
            record_db_query(success=False)
            raise

    async def execute(self, query, *args, **kwargs):
        try:
            result = await self._conn.execute(query, *args, **kwargs)
            record_db_query(success=True)
            return result
        except Exception:
            record_db_query(success=False)
            raise

    async def executemany(self, query, *args, **kwargs):
        try:
            result = await self._conn.executemany(query, *args, **kwargs)
            record_db_query(success=True)
            return result
        except Exception:
            record_db_query(success=False)
            raise
