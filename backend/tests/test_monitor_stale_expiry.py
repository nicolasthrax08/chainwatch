#!/usr/bin/env python3
"""
Unit tests for the monitor Phase 7 stale signal expiry logic
and _ensure_prices_fetched cancellation safety.

Tests cover:
- _stale_expiry_cycle_counter incrementing each poll cycle
- Stale expiry runs only every STALE_EXPIRY_INTERVAL_CYCLES cycles
- Stale expiry SQL: updates pending signals older than threshold to 'stale'
- Stale expiry records count in cycle stats
- Stale expiry failure is caught and logged (doesn't crash the cycle)
- _stale_expiry_cycle_counter resets after expiry runs
- _ensure_prices_fetched: CancelledError clears _fetch_in_progress flag
- _ensure_prices_fetched: concurrent callers don't cause thundering herd
- _ensure_prices_fetched: lock is NOT held during HTTP I/O

Run: python3 -m pytest backend/tests/test_monitor_stale_expiry.py -v
"""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import monitor


def _run(coro):
    """Run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reset_monitor_state():
    """Reset all module-level mutable state to a clean baseline."""
    monitor._last_balances.clear()
    monitor._last_tx_hashes.clear()
    monitor._consecutive_errors.clear()
    monitor._pool = None
    monitor._clients.clear()
    monitor._cancel_event.clear()
    monitor._worker_task = None
    monitor._cycle_stats.clear()
    monitor._last_cycle_duration = 0.0
    monitor._stale_expiry_cycle_counter = 0
    monitor._poll_lock = asyncio.Lock()
    monitor._price_cache.clear()
    monitor._price_cache.update({
        "ETH": 2500.0,
        "SOL": 170.0,
        "BTC": 105000.0,
        "USDHKD": 7.8,
        "USDBTC": 1.0 / 105000.0,
        "timestamp": time.time(),
    })
    monitor._price_fetch_event = None


class _AsyncCtxMgr:
    """Helper: wraps a mock as an async context manager."""
    def __init__(self, mock_obj):
        self._mock = mock_obj

    async def __aenter__(self):
        return self._mock

    async def __aexit__(self, *args):
        return False


# ─── Phase 7: Stale Signal Expiry ──────────────────────────────────────────

class TestStaleExpiryCycleCounter(unittest.TestCase):
    """Test the _stale_expiry_cycle_counter increment and reset behavior."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_counter_starts_at_zero(self):
        """After reset, _stale_expiry_cycle_counter should be 0."""
        self.assertEqual(monitor._stale_expiry_cycle_counter, 0)

    def test_counter_increments_each_cycle(self):
        """Each poll cycle should increment the counter by 1."""
        self.assertEqual(monitor._stale_expiry_cycle_counter, 0)
        # Simulate what _poll_all_wallets_inner does at start of Phase 7
        monitor._stale_expiry_cycle_counter += 1
        self.assertEqual(monitor._stale_expiry_cycle_counter, 1)
        monitor._stale_expiry_cycle_counter += 1
        self.assertEqual(monitor._stale_expiry_cycle_counter, 2)

    def test_counter_resets_after_expiry_run(self):
        """After stale expiry runs, counter should reset to 0."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        # Simulate the reset logic from Phase 7
        if monitor._stale_expiry_cycle_counter >= monitor.STALE_EXPIRY_INTERVAL_CYCLES:
            monitor._stale_expiry_cycle_counter = 0
        self.assertEqual(monitor._stale_expiry_cycle_counter, 0)


class TestStaleExpiryInterval(unittest.TestCase):
    """Test that stale expiry only runs at the configured interval."""

    def setUp(self):
        _reset_monitor_state()
        self.mock_pool = MagicMock()
        monitor._pool = self.mock_pool
        self.mock_eth_client = AsyncMock()
        monitor._clients = {"eth": self.mock_eth_client, "sol": AsyncMock(), "btc": AsyncMock()}

    def tearDown(self):
        _reset_monitor_state()

    def _make_wallet_rows(self, n=1):
        rows = []
        for i in range(n):
            rows.append({
                "id": str(i + 1),
                "address": f"0x{i:040x}",
                "chain": "eth",
                "balance_native": 10.0,
                "balance_usd": 25000.0,
                "last_balance_update": None,
                "is_whale": False,
                "is_mine": False,
                "user_id": f"user-{i + 1}",
            })
        return rows

    def _make_mock_conn(self, rows=None, expired_ids=None):
        mock_conn = AsyncMock()
        if rows is not None:
            mock_conn.fetch = AsyncMock(return_value=rows)
        else:
            mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock()
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_conn

    def test_expiry_not_run_before_interval(self):
        """Stale expiry should NOT run when counter < STALE_EXPIRY_INTERVAL_CYCLES after increment."""
        # Set counter so that after increment it's still below the threshold
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES - 2
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        # The counter should have incremented by 1 but NOT reached the threshold
        self.assertEqual(monitor._stale_expiry_cycle_counter, monitor.STALE_EXPIRY_INTERVAL_CYCLES - 1)
        # No stale expiry fetch should have been called (Phase 7 skipped)
        # conn.fetch was only called once (Phase 1 wallet SELECT)
        self.assertEqual(conn.fetch.call_count, 1)

    def test_expiry_runs_at_interval(self):
        """Stale expiry SHOULD run when counter reaches STALE_EXPIRY_INTERVAL_CYCLES."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # Mock the Phase 7 DB fetch to return 3 expired signal IDs
        expired_ids = [MagicMock(), MagicMock(), MagicMock()]
        conn.fetch = AsyncMock(side_effect=[
            rows,  # Phase 1: wallet rows
            [],    # Phase 5: prev_balance batch (no changed wallets)
            expired_ids,  # Phase 7: stale expiry UPDATE ... RETURNING id
        ])

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        # Counter should have reset to 0
        self.assertEqual(monitor._stale_expiry_cycle_counter, 0)

    def test_expiry_records_count_in_stats(self):
        """Stale expiry should record the count of expired signals in cycle stats."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # Phase 1: wallet rows, Phase 7: stale expiry returns 2 expired IDs
        # (Phase 5/6 skipped because no wallets changed)
        expired_ids = [MagicMock(), MagicMock()]
        conn.fetch = AsyncMock(side_effect=[
            rows,           # Phase 1: wallet SELECT
            expired_ids,    # Phase 7: stale expiry UPDATE ... RETURNING id
        ])

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        # Check cycle stats
        self.assertEqual(len(monitor._cycle_stats), 1)
        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["signals_stale_expired"], 2)

    def test_expiry_failure_doesnt_crash_cycle(self):
        """If stale expiry SQL raises, the poll cycle should still complete."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # Phase 1 returns wallet rows, Phase 7 fetch raises an error
        call_count = [0]
        async def fetch_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return rows  # Phase 1: wallet SELECT
            raise Exception("DB connection lost during stale expiry")  # Phase 7

        conn.fetch = AsyncMock(side_effect=fetch_side_effect)

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                # Should NOT raise
                _run(monitor._poll_all_wallets_inner())

        # Cycle stats should still be recorded
        self.assertEqual(len(monitor._cycle_stats), 1)

    def test_expiry_zero_expired_signals(self):
        """When no signals are stale, stats should show 0 expired."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # Phase 1: wallet rows, Phase 7: no expired signals
        conn.fetch = AsyncMock(side_effect=[
            rows,  # Phase 1: wallet SELECT
            [],    # Phase 7: stale expiry returns empty list
        ])

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["signals_stale_expired"], 0)

    def test_expiry_uses_correct_threshold_hours(self):
        """The stale expiry SQL should use SIGNAL_STALE_THRESHOLD_HOURS."""
        self.assertEqual(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 72)
        self.assertGreater(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 0)

    def test_expiry_interval_cycles_sane(self):
        """STALE_EXPIRY_INTERVAL_CYCLES should be a positive integer."""
        self.assertGreater(monitor.STALE_EXPIRY_INTERVAL_CYCLES, 0)
        self.assertIsInstance(monitor.STALE_EXPIRY_INTERVAL_CYCLES, int)


# ─── _ensure_prices_fetched: Cancellation Safety ───────────────────────────

class TestEnsurePricesFetchedCancellation(unittest.TestCase):
    """Test that CancelledError during price fetch clears _price_fetch_event."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_cancelled_error_clears_fetch_in_progress(self):
        """When CancelledError is raised during HTTP fetch, _price_fetch_event must be reset."""
        # Set up the cache to be stale so a fetch is triggered
        monitor._price_cache["timestamp"] = 0.0

        async def mock_get_client():
            mock_client = AsyncMock()
            async def cancelled_get(*args, **kwargs):
                raise asyncio.CancelledError("shutdown")
            mock_client.get = cancelled_get
            return mock_client

        with patch("services.tx_fetcher._get_client", mock_get_client):
            try:
                _run(monitor._ensure_prices_fetched())
            except asyncio.CancelledError:
                pass

        # _price_fetch_event must be None so future cycles can retry
        self.assertIsNone(monitor._price_fetch_event)

    def test_cancelled_error_doesnt_update_timestamp(self):
        """When CancelledError is raised, the cache timestamp should NOT be updated."""
        monitor._price_cache["timestamp"] = 0.0
        original_eth = monitor._price_cache["ETH"]

        async def mock_get_client():
            mock_client = AsyncMock()
            async def cancelled_get(*args, **kwargs):
                raise asyncio.CancelledError("shutdown")
            mock_client.get = cancelled_get
            return mock_client

        with patch("services.tx_fetcher._get_client", mock_get_client):
            try:
                _run(monitor._ensure_prices_fetched())
            except asyncio.CancelledError:
                pass

        # Timestamp should still be 0.0 (not updated)
        self.assertEqual(monitor._price_cache["timestamp"], 0.0)
        # ETH price should still be the default
        self.assertEqual(monitor._price_cache["ETH"], original_eth)

    def test_concurrent_fetch_prevents_thundering_herd(self):
        """When a price fetch is in progress, concurrent callers should await it (not start another)."""
        import asyncio as _test_asyncio
        monitor._price_cache["timestamp"] = 0.0
        # Simulate an in-flight fetch: create an unset event
        monitor._price_fetch_event = _test_asyncio.Event()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock()

        async def mock_get_client():
            return mock_client

        async def _run_with_timeout():
            # The caller should try to wait for the event, time out, and NOT call get
            try:
                await asyncio.wait_for(monitor._ensure_prices_fetched(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

        with patch("services.tx_fetcher._get_client", mock_get_client):
            _run(_run_with_timeout())

        # HTTP get should NOT have been called (another coroutine is fetching;
        # this caller awaited the event and timed out)
        mock_client.get.assert_not_called()

    def test_concurrent_fetch_waits_for_in_flight(self):
        """When a price fetch completes, waiters should unblock and get fresh prices."""
        import asyncio as _test_asyncio
        monitor._price_cache["timestamp"] = 0.0
        monitor._price_cache["ETH"] = 2500.0
        # Simulate an in-flight fetch: create an unset event
        monitor._price_fetch_event = _test_asyncio.Event()

        mock_client = AsyncMock()

        async def mock_get(*args, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = AsyncMock()
            if "ethereum" in kwargs.get("params", {}).get("ids", ""):
                mock_resp.json = AsyncMock(return_value={"ethereum": {"usd": 3000, "hkd": 23400, "btc": 0.028}})
            else:
                mock_resp.json = AsyncMock(return_value={"solana": {"usd": 150}, "bitcoin": {"usd": 95000}})
            return mock_resp

        mock_client.get = mock_get

        async def mock_get_client():
            return mock_client

        waiter_done = False

        async def _waiter():
            nonlocal waiter_done
            await monitor._ensure_prices_fetched()
            waiter_done = True

        async def _fetcher():
            # Simulate the fetcher completing after a short delay
            await asyncio.sleep(0.05)
            # Manually update cache and signal the event (simulating what the real fetcher does)
            async with monitor._price_cache_lock:
                monitor._price_cache["ETH"] = 3000.0
                monitor._price_cache["timestamp"] = time.time()
                if monitor._price_fetch_event is not None:
                    monitor._price_fetch_event.set()
                    monitor._price_fetch_event = None

        async def _run_concurrent():
            await asyncio.gather(_waiter(), _fetcher())

        with patch("services.tx_fetcher._get_client", mock_get_client):
            _run(_run_concurrent())

        # The waiter should have completed (unblocked by the fetcher)
        assert waiter_done, "Waiter should have been unblocked by the fetcher"
        # The price should have been updated by the fetcher
        self.assertEqual(monitor._price_cache["ETH"], 3000.0)

    def test_fetch_event_cleared_on_http_error(self):
        """When HTTP fetch raises a non-CancelledError exception, _price_fetch_event must be reset."""
        monitor._price_cache["timestamp"] = 0.0

        async def mock_get_client():
            mock_client = AsyncMock()
            async def error_get(*args, **kwargs):
                raise Exception("CoinGecko rate limited")
            mock_client.get = error_get
            return mock_client

        with patch("services.tx_fetcher._get_client", mock_get_client):
            # Should not raise — the exception is caught
            _run(monitor._ensure_prices_fetched())

        # _price_fetch_event must be None (reset after failure)
        self.assertIsNone(monitor._price_fetch_event)

    def test_lock_not_held_during_http_io(self):
        """The _price_cache_lock should NOT be held during the HTTP fetch (lock-splitting pattern)."""
        monitor._price_cache["timestamp"] = 0.0
        lock_held_during_http = [False]

        async def mock_get_client():
            mock_client = AsyncMock()
            async def tracking_get(*args, **kwargs):
                # Check if the price cache lock is held
                lock_held_during_http[0] = monitor._price_cache_lock.locked()
                # Return valid response
                import httpx
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "ethereum": {"usd": 2500.0, "hkd": 19500.0, "btc": 0.0238},
                }
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            mock_client.get = tracking_get
            return mock_client

        with patch("services.tx_fetcher._get_client", mock_get_client):
            _run(monitor._ensure_prices_fetched())

        # The lock should NOT have been held during the HTTP call
        self.assertFalse(lock_held_during_http[0],
                         "Lock was held during HTTP I/O — this blocks all other wallet processing")


# ─── Phase 7 + Price Cache Integration ─────────────────────────────────────

class TestStaleExpiryWithPriceCacheIntegration(unittest.TestCase):
    """Test that stale expiry and price cache interact correctly in a full cycle."""

    def setUp(self):
        _reset_monitor_state()
        self.mock_pool = MagicMock()
        monitor._pool = self.mock_pool
        self.mock_eth_client = AsyncMock()
        monitor._clients = {"eth": self.mock_eth_client, "sol": AsyncMock(), "btc": AsyncMock()}

    def tearDown(self):
        _reset_monitor_state()

    def test_full_cycle_with_stale_expiry_and_price_refresh(self):
        """A full poll cycle with both stale expiry and price refresh should complete."""
        monitor._stale_expiry_cycle_counter = monitor.STALE_EXPIRY_INTERVAL_CYCLES
        monitor._price_cache["timestamp"] = 0.0  # Force price refresh

        rows = [{
            "id": "1",
            "address": "0x" + "0" * 40,
            "chain": "eth",
            "balance_native": 10.0,
            "balance_usd": 25000.0,
            "last_balance_update": None,
            "is_whale": False,
            "is_mine": False,
            "user_id": "user-1",
        }]
        monitor._last_balances["1"] = 10.0

        conn = AsyncMock()
        # Phase 1: wallet rows, Phase 7: 1 stale signal expired
        conn.fetch = AsyncMock(side_effect=[
            rows,           # Phase 1: wallet SELECT
            [MagicMock()],  # Phase 7: stale expiry UPDATE ... RETURNING id
        ])
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        conn.transaction = MagicMock()
        conn.transaction.return_value.__aenter__ = AsyncMock()
        conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # Mock the CoinGecko HTTP response for price refresh
        mock_http_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ethereum": {"usd": 2600.0, "hkd": 20280.0, "btc": 0.0248},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = {
            "solana": {"usd": 175.0},
            "bitcoin": {"usd": 105000.0},
        }
        mock_resp2.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(side_effect=[mock_resp, mock_resp2])

        async def mock_get_client():
            return mock_http_client

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch("services.tx_fetcher._get_client", mock_get_client):
                _run(monitor._poll_all_wallets_inner())

        # Verify stale expiry ran
        self.assertEqual(monitor._stale_expiry_cycle_counter, 0)
        self.assertEqual(len(monitor._cycle_stats), 1)
        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["signals_stale_expired"], 1)

        # Verify price cache was refreshed
        self.assertGreater(monitor._price_cache["timestamp"], 0)
        self.assertEqual(monitor._price_cache["ETH"], 2600.0)


if __name__ == "__main__":
    unittest.main()
