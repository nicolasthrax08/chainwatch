#!/usr/bin/env python3
"""
Unit tests for the monitor.py price cache (_ensure_prices_fetched).

Tests cover:
- Cache hit: returns immediately when cache is fresh
- Cache miss with no in-progress fetch: triggers fetch
- Cache miss with fetch already in progress: skips (thundering herd prevention)
- Successful fetch: updates cache with new prices
- CoinGecko API failure: keeps stale values, clears _fetch_in_progress
- CancelledError: clears _fetch_in_progress and re-raises
- USDHKD and USDBTC cross-rate computation
- Fallback to stale values when new price is 0

Run: python3 -m pytest tests/test_monitor_price_cache.py -v
"""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import monitor, tx_fetcher as _tx_fetcher_module


def _run(coro):
    """Run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reset_price_cache():
    """Reset the price cache to a known state."""
    monitor._price_cache.clear()
    monitor._price_cache.update({
        "ETH": 2500.0,
        "SOL": 170.0,
        "BTC": 105000.0,
        "USDHKD": 7.8,
        "USDBTC": 1.0 / 105000.0,
        "timestamp": 0.0,
    })


class TestEnsurePricesFetchedFreshCache(unittest.TestCase):
    """Test that a fresh cache returns immediately without fetching."""

    def setUp(self):
        _reset_price_cache()

    def test_fresh_cache_returns_immediately(self):
        """When cache timestamp is < 60s old, should return without fetching."""
        monitor._price_cache["timestamp"] = time.time() - 30  # 30s ago
        # If it tries to fetch, the mock will raise
        with patch.object(_tx_fetcher_module, "_get_client", side_error=Exception("should not fetch")):
            _run(monitor._ensure_prices_fetched())
        # Cache should be unchanged
        self.assertEqual(monitor._price_cache["ETH"], 2500.0)

    def test_zero_timestamp_triggers_fetch(self):
        """When timestamp is 0, cache is cold and should trigger fetch."""
        monitor._price_cache["timestamp"] = 0.0

        mock_client = AsyncMock()
        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = {"ethereum": {"usd": 3000, "hkd": 23400, "btc": 0.0286}}
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = {"solana": {"usd": 200}, "bitcoin": {"usd": 105000}}
        mock_client.get = AsyncMock(side_effect=[mock_resp1, mock_resp2])

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())

        # Should have updated
        self.assertEqual(monitor._price_cache["ETH"], 3000.0)

    def test_negative_timestamp_triggers_fetch(self):
        """A very old timestamp should trigger a fetch."""
        monitor._price_cache["timestamp"] = 1.0  # Epoch+1, very old

        mock_client = AsyncMock()
        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = {"ethereum": {"usd": 3000, "hkd": 23400, "btc": 0.0286}}
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = {"solana": {"usd": 200}, "bitcoin": {"usd": 105000}}
        mock_client.get = AsyncMock(side_effect=[mock_resp1, mock_resp2])

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())

        self.assertEqual(monitor._price_cache["ETH"], 3000.0)


class TestEnsurePricesFetchedInProgress(unittest.TestCase):
    """Test the thundering herd prevention flag."""

    def setUp(self):
        _reset_price_cache()

    def test_fetch_in_progress_skips(self):
        """When _fetch_in_progress is True, should return immediately."""
        monitor._price_cache["timestamp"] = 0.0  # Would normally trigger fetch
        monitor._price_cache["_fetch_in_progress"] = True

        # If it tries to fetch, the mock will raise
        with patch.object(_tx_fetcher_module, "_get_client", side_effect=Exception("should not fetch")):
            _run(monitor._ensure_prices_fetched())

        # _fetch_in_progress should still be True (not cleared because we skipped)
        self.assertTrue(monitor._price_cache.get("_fetch_in_progress", False))

    def test_sets_fetch_in_progress_before_io(self):
        """Should set _fetch_in_progress=True before releasing lock for I/O."""
        monitor._price_cache["timestamp"] = 0.0
        monitor._price_cache["_fetch_in_progress"] = False

        call_order = []

        original_lock_enter = monitor._price_cache_lock.__aenter__
        original_lock_exit = monitor._price_cache_lock.__aexit__

        # Track lock state
        lock_held = False

        async def tracking_lock_enter(*args, **kwargs):
            nonlocal lock_held
            lock_held = True
            return await original_lock_enter(*args, **kwargs)

        async def tracking_lock_exit(*args, **kwargs):
            nonlocal lock_held
            lock_held = False
            return await original_lock_exit(*args, **kwargs)

        mock_client = AsyncMock()
        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = {"ethereum": {"usd": 3000, "hkd": 23400, "btc": 0.0286}}
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = {"solana": {"usd": 200}, "bitcoin": {"usd": 105000}}

        async def tracking_get(*args, **kwargs):
            # During the HTTP call, lock should NOT be held
            call_order.append(("http_call", lock_held))
            if len(call_order) == 1:
                return mock_resp1
            return mock_resp2

        mock_client.get = tracking_get

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            with patch.object(monitor._price_cache_lock, "__aenter__", tracking_lock_enter):
                with patch.object(monitor._price_cache_lock, "__aexit__", tracking_lock_exit):
                    _run(monitor._ensure_prices_fetched())

        # During HTTP calls, lock should not be held
        for call_name, held in call_order:
            if call_name == "http_call":
                self.assertFalse(held, "Lock should NOT be held during HTTP I/O")


class TestEnsurePricesFetchedSuccess(unittest.TestCase):
    """Test successful price fetch and cache update."""

    def setUp(self):
        _reset_price_cache()

    def _make_mock_client(self, eth_usd=3000, eth_hkd=23400, eth_btc=0.0286,
                          sol_usd=200, btc_usd=105000):
        mock_client = AsyncMock()
        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = {
            "ethereum": {"usd": eth_usd, "hkd": eth_hkd, "btc": eth_btc}
        }
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = {
            "solana": {"usd": sol_usd},
            "bitcoin": {"usd": btc_usd},
        }
        mock_client.get = AsyncMock(side_effect=[mock_resp1, mock_resp2])
        return mock_client

    def test_eth_price_updated(self):
        """ETH price should be updated from CoinGecko response."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client()
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        self.assertEqual(monitor._price_cache["ETH"], 3000.0)

    def test_sol_price_updated(self):
        """SOL price should be updated from CoinGecko response."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client()
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        self.assertEqual(monitor._price_cache["SOL"], 200.0)

    def test_btc_price_updated(self):
        """BTC price should be updated from CoinGecko response."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client()
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        self.assertEqual(monitor._price_cache["BTC"], 105000.0)

    def test_usdhkd_computed_from_eth(self):
        """USDHKD cross-rate should be computed as eth_hkd / eth_usd."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client(eth_usd=3000, eth_hkd=23400)
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        expected = 23400 / 3000  # 7.8
        self.assertAlmostEqual(monitor._price_cache["USDHKD"], expected, places=4)

    def test_usdbtc_computed_from_eth(self):
        """USDBTC cross-rate should be computed as eth_btc / eth_usd."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client(eth_usd=3000, eth_btc=0.0286)
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        expected = 0.0286 / 3000
        self.assertAlmostEqual(monitor._price_cache["USDBTC"], expected, places=8)

    def test_timestamp_updated(self):
        """Cache timestamp should be updated after successful fetch."""
        monitor._price_cache["timestamp"] = 0.0
        before = time.time()
        mock_client = self._make_mock_client()
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        after = time.time()
        self.assertGreaterEqual(monitor._price_cache["timestamp"], before)
        self.assertLessEqual(monitor._price_cache["timestamp"], after)

    def test_fetch_in_progress_cleared_after_success(self):
        """_fetch_in_progress should be cleared after successful fetch."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client()
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        self.assertFalse(monitor._price_cache.get("_fetch_in_progress", False))

    def test_stale_fallback_when_new_price_is_zero(self):
        """When CoinGecko returns 0 for a price, should keep the stale value."""
        monitor._price_cache["timestamp"] = 0.0
        # SOL returns 0 — should keep stale 170.0
        mock_client = self._make_mock_client(sol_usd=0)
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        self.assertEqual(monitor._price_cache["SOL"], 170.0)

    def test_concurrent_httpx_calls(self):
        """Both CoinGecko requests should be fired concurrently via asyncio.gather."""
        monitor._price_cache["timestamp"] = 0.0
        call_times = []

        mock_client = AsyncMock()
        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = {"ethereum": {"usd": 3000, "hkd": 23400, "btc": 0.0286}}
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = {"solana": {"usd": 200}, "bitcoin": {"usd": 105000}}

        async def slow_get(*args, **kwargs):
            call_times.append(time.time())
            await asyncio.sleep(0.01)
            if len(call_times) == 1:
                return mock_resp1
            return mock_resp2

        mock_client.get = slow_get

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())

        # Both calls should have been made
        self.assertEqual(len(call_times), 2)
        # They should be nearly concurrent (within 5ms of each other)
        self.assertLess(abs(call_times[0] - call_times[1]), 0.05)

    def test_no_usdhkd_when_eth_usd_is_zero(self):
        """When eth_usd is 0, USDHKD should not be computed (would be division by zero)."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = self._make_mock_client(eth_usd=0)
        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())
        # USDHKD should remain at stale value
        self.assertEqual(monitor._price_cache["USDHKD"], 7.8)


class TestEnsurePricesFetchedFailure(unittest.TestCase):
    """Test graceful handling of CoinGecko API failures."""

    def setUp(self):
        _reset_price_cache()

    def test_http_error_keeps_stale_values(self):
        """HTTP error should leave cache at stale values."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("CoinGecko 500"))

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())

        # Should still have stale values
        self.assertEqual(monitor._price_cache["ETH"], 2500.0)
        self.assertEqual(monitor._price_cache["SOL"], 170.0)
        self.assertEqual(monitor._price_cache["BTC"], 105000.0)

    def test_http_error_clears_fetch_in_progress(self):
        """HTTP error should clear _fetch_in_progress so retry can happen."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("CoinGecko 500"))

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            _run(monitor._ensure_prices_fetched())

        self.assertFalse(monitor._price_cache.get("_fetch_in_progress", False))

    def test_cancelled_error_clears_fetch_and_reraises(self):
        """CancelledError should clear _fetch_in_progress and re-raise."""
        monitor._price_cache["timestamp"] = 0.0
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=asyncio.CancelledError())

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            with self.assertRaises(asyncio.CancelledError):
                _run(monitor._ensure_prices_fetched())

        # _fetch_in_progress must be cleared even on cancellation
        self.assertFalse(monitor._price_cache.get("_fetch_in_progress", False))

    def test_cancelled_error_does_not_update_cache(self):
        """CancelledError should not update the cache with partial data."""
        monitor._price_cache["timestamp"] = 0.0
        eth_before = monitor._price_cache["ETH"]

        mock_client = AsyncMock()

        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        # Both calls raise CancelledError
        mock_client.get = AsyncMock(side_effect=raise_cancelled)

        with patch.object(_tx_fetcher_module, "_get_client", return_value=mock_client):
            with self.assertRaises(asyncio.CancelledError):
                _run(monitor._ensure_prices_fetched())

        # Cache should NOT have been updated
        self.assertEqual(monitor._price_cache["ETH"], eth_before)


class TestPriceCacheDefaults(unittest.TestCase):
    """Test that the price cache has sensible defaults."""

    def test_default_prices_are_nonzero(self):
        """All default prices should be > 0 (Pitfall #5: cache init to 0)."""
        self.assertGreater(monitor._price_cache["ETH"], 0)
        self.assertGreater(monitor._price_cache["SOL"], 0)
        self.assertGreater(monitor._price_cache["BTC"], 0)
        self.assertGreater(monitor._price_cache["USDHKD"], 0)
        self.assertGreater(monitor._price_cache["USDBTC"], 0)

    def test_default_timestamp_is_zero(self):
        """Default timestamp should be 0 (cold start, triggers first fetch)."""
        self.assertEqual(monitor._price_cache["timestamp"], 0.0)

    def test_default_usdbtc_is_inverse_of_btc(self):
        """Default USDBTC should be approximately 1/BTC."""
        expected = 1.0 / monitor._price_cache["BTC"]
        self.assertAlmostEqual(monitor._price_cache["USDBTC"], expected, places=12)


if __name__ == "__main__":
    unittest.main()
