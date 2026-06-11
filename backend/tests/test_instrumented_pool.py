"""
Tests for services/instrumented_pool.py
========================================
DB pool instrumentation wrapper that records query metrics.
Tests cover: pool instrumentation, connection wrapping, success/error recording.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from services.instrumented_pool import instrument_pool, _InstrumentedConnection


class TestInstrumentPool:
    """instrument_pool() function."""

    def test_none_pool_returns_none(self):
        """Should handle None pool gracefully."""
        result = instrument_pool(None)
        assert result is None

    def test_patches_acquire_method(self):
        """Should replace pool.acquire with instrumented version."""
        pool = MagicMock()
        original_acquire = pool.acquire
        instrument_pool(pool)
        # acquire should have been replaced
        assert pool.acquire is not original_acquire

    @pytest.mark.asyncio
    async def test_instrumented_acquire_returns_instrumented_connection(self):
        """Instrumented acquire should return _InstrumentedConnection."""
        pool = MagicMock()
        real_conn = MagicMock()
        pool.acquire = AsyncMock(return_value=real_conn)

        instrument_pool(pool)

        result = await pool.acquire()
        assert isinstance(result, _InstrumentedConnection)

    @pytest.mark.asyncio
    async def test_instrumented_acquire_passes_args(self):
        """Instrumented acquire should pass through args/kwargs."""
        pool = MagicMock()
        real_conn = MagicMock()
        original_acquire = AsyncMock(return_value=real_conn)
        pool.acquire = original_acquire

        instrument_pool(pool)

        await pool.acquire("arg1", key="val")
        original_acquire.assert_called_once_with("arg1", key="val")


class TestInstrumentedConnection:
    """_InstrumentedConnection wrapper."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    def test_getattr_delegates_to_real_conn(self):
        """Attribute access should delegate to underlying connection."""
        self.real_conn.some_attr = "value"
        assert self.conn.some_attr == "value"

    def test_getattr_delegates_methods(self):
        """Method access should delegate to underlying connection."""
        self.real_conn.custom_method = MagicMock(return_value=42)
        result = self.conn.custom_method()
        assert result == 42

    @pytest.mark.asyncio
    async def test_aenter_delegates(self):
        """__aenter__ should delegate to real connection."""
        self.real_conn.__aenter__ = AsyncMock(return_value=self.real_conn)
        result = await self.conn.__aenter__()
        assert result is self.real_conn

    @pytest.mark.asyncio
    async def test_aexit_delegates(self):
        """__aexit__ should delegate to real connection."""
        self.real_conn.__aexit__ = AsyncMock(return_value=False)
        await self.conn.__aexit__(None, None, None)
        self.real_conn.__aexit__.assert_called_once_with(None, None, None)


class TestInstrumentedConnectionFetch:
    """fetch() method instrumentation."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    @pytest.mark.asyncio
    async def test_fetch_success_records_and_returns(self):
        """Successful fetch should record success and return result."""
        expected = [{"id": 1}, {"id": 2}]
        self.real_conn.fetch = AsyncMock(return_value=expected)

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            result = await self.conn.fetch("SELECT * FROM wallets")
            assert result == expected
            mock_record.assert_called_once_with(success=True)

    @pytest.mark.asyncio
    async def test_fetch_error_records_and_raises(self):
        """Failed fetch should record failure and re-raise."""
        self.real_conn.fetch = AsyncMock(side_effect=Exception("DB error"))

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            with pytest.raises(Exception, match="DB error"):
                await self.conn.fetch("SELECT * FROM wallets")
            mock_record.assert_called_once_with(success=False)

    @pytest.mark.asyncio
    async def test_fetch_passes_query_and_args(self):
        """fetch should pass query and args to real connection."""
        self.real_conn.fetch = AsyncMock(return_value=[])

        with patch("services.instrumented_pool.record_db_query"):
            await self.conn.fetch("SELECT * FROM t WHERE id = $1", 42)
            self.real_conn.fetch.assert_called_once_with("SELECT * FROM t WHERE id = $1", 42)


class TestInstrumentedConnectionFetchrow:
    """fetchrow() method instrumentation."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    @pytest.mark.asyncio
    async def test_fetchrow_success(self):
        self.real_conn.fetchrow = AsyncMock(return_value={"id": 1})

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            result = await self.conn.fetchrow("SELECT 1")
            assert result == {"id": 1}
            mock_record.assert_called_once_with(success=True)

    @pytest.mark.asyncio
    async def test_fetchrow_error(self):
        self.real_conn.fetchrow = AsyncMock(side_effect=RuntimeError("fail"))

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            with pytest.raises(RuntimeError, match="fail"):
                await self.conn.fetchrow("SELECT 1")
            mock_record.assert_called_once_with(success=False)


class TestInstrumentedConnectionFetchval:
    """fetchval() method instrumentation."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    @pytest.mark.asyncio
    async def test_fetchval_success(self):
        self.real_conn.fetchval = AsyncMock(return_value=42)

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            result = await self.conn.fetchval("SELECT COUNT(*) FROM t")
            assert result == 42
            mock_record.assert_called_once_with(success=True)

    @pytest.mark.asyncio
    async def test_fetchval_error(self):
        self.real_conn.fetchval = AsyncMock(side_effect=ValueError("bad"))

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            with pytest.raises(ValueError, match="bad"):
                await self.conn.fetchval("SELECT 1")
            mock_record.assert_called_once_with(success=False)


class TestInstrumentedConnectionExecute:
    """execute() method instrumentation."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    @pytest.mark.asyncio
    async def test_execute_success(self):
        self.real_conn.execute = AsyncMock(return_value="INSERT 0 1")

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            result = await self.conn.execute("INSERT INTO t VALUES (1)")
            assert result == "INSERT 0 1"
            mock_record.assert_called_once_with(success=True)

    @pytest.mark.asyncio
    async def test_execute_error(self):
        self.real_conn.execute = AsyncMock(side_effect=Exception("constraint"))

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            with pytest.raises(Exception, match="constraint"):
                await self.conn.execute("INSERT INTO t VALUES (1)")
            mock_record.assert_called_once_with(success=False)

    @pytest.mark.asyncio
    async def test_execute_passes_args(self):
        self.real_conn.execute = AsyncMock(return_value="INSERT 0 1")

        with patch("services.instrumented_pool.record_db_query"):
            await self.conn.execute("INSERT INTO t VALUES ($1)", "value")
            self.real_conn.execute.assert_called_once_with("INSERT INTO t VALUES ($1)", "value")


class TestInstrumentedConnectionExecutemany:
    """executemany() method instrumentation."""

    def setup_method(self):
        self.real_conn = MagicMock()
        self.conn = _InstrumentedConnection(self.real_conn)

    @pytest.mark.asyncio
    async def test_executemany_success(self):
        self.real_conn.executemany = AsyncMock(return_value="INSERT 0 3")

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            result = await self.conn.executemany("INSERT INTO t VALUES ($1)", [(1,), (2,), (3,)])
            assert result == "INSERT 0 3"
            mock_record.assert_called_once_with(success=True)

    @pytest.mark.asyncio
    async def test_executemany_error(self):
        self.real_conn.executemany = AsyncMock(side_effect=Exception("batch fail"))

        with patch("services.instrumented_pool.record_db_query") as mock_record:
            with pytest.raises(Exception, match="batch fail"):
                await self.conn.executemany("INSERT INTO t VALUES ($1)", [(1,)])
            mock_record.assert_called_once_with(success=False)
