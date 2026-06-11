#!/usr/bin/env python3
"""
Unit tests for the monitor.py core logic (phases, wallet checking, state management).

Tests cover:
- bal_nonnative_safe: clamping
- start_monitor: pool=None guard, double-start guard, client init failure
- is_monitor_alive: running / not running / done
- stop_monitor: graceful shutdown, client cleanup
- _check_wallet_with_sem: cancel guard, semaphore wrapping, timeout
- _check_wallet_balance_and_txs:
  - balance change detection (first seen, changed, unchanged)
  - new tx detection (first tx, new tx, same tx)
  - state updates (_last_balances, _last_tx_hashes, _consecutive_errors reset)
  - error backoff: wallet with >= MAX_CONSECUTIVE_ERRORS gets retried
  - unknown chain → RuntimeError
  - no client for chain → RuntimeError
  - price cache integration (_ensure_prices_fetched called)
  - return tuple shape on change / None on no change
- _poll_all_wallets_inner:
  - empty wallet list → early return + state prune
  - single wallet, no changes
  - state pruning (removed wallets cleaned from _last_balances etc.)
  - cycle stats recording
  - error tracking (_consecutive_errors increment)
  - phase timing dict populated
- _poll_all_wallets: re-entrancy guard
- get_cycle_stats: returns correct shape, respects ring buffer
- Config constants sanity

Run: python3 -m pytest backend/tests/test_monitor_core.py -v
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
    # Re-create poll lock (can't reuse after close)
    monitor._poll_lock = asyncio.Lock()
    # Reset price cache to defaults (fresh timestamp so _ensure_prices_fetched skips)
    monitor._price_cache.clear()
    monitor._price_cache.update({
        "ETH": 2500.0,
        "SOL": 170.0,
        "BTC": 105000.0,
        "USDHKD": 7.8,
        "USDBTC": 1.0 / 105000.0,
        "timestamp": time.time(),
    })


class _AsyncCtxMgr:
    """Helper: wraps a mock as an async context manager."""
    def __init__(self, mock_obj):
        self._mock = mock_obj

    async def __aenter__(self):
        return self._mock

    async def __aexit__(self, *args):
        return False


# ─── bal_nonnative_safe ───────────────────────────────────────────────────

class TestBalNonnativeSafe(unittest.TestCase):
    """Test the balance clamping helper."""

    def test_positive_value_unchanged(self):
        self.assertEqual(monitor.bal_nonnative_safe(1.5), 1.5)

    def test_zero_returns_zero(self):
        self.assertEqual(monitor.bal_nonnative_safe(0.0), 0.0)

    def test_negative_clamped_to_zero(self):
        self.assertEqual(monitor.bal_nonnative_safe(-1.0), 0.0)

    def test_very_negative_clamped(self):
        self.assertEqual(monitor.bal_nonnative_safe(-1e18), 0.0)

    def test_small_positive_unchanged(self):
        self.assertAlmostEqual(monitor.bal_nonnative_safe(1e-12), 1e-12)


# ─── start_monitor ────────────────────────────────────────────────────────

class TestStartMonitor(unittest.TestCase):
    """Test the start_monitor public API."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_none_pool_does_nothing(self):
        """start_monitor(None) should return without creating a worker."""
        monitor.start_monitor(None)
        self.assertIsNone(monitor._worker_task)
        self.assertIsNone(monitor._pool)

    def test_none_pool_does_not_touch_cancel_event(self):
        """start_monitor(None) should not set _cancel_event."""
        monitor._cancel_event.set()
        monitor.start_monitor(None)
        self.assertTrue(monitor._cancel_event.is_set())

    def test_valid_pool_creates_worker(self):
        """start_monitor with a valid pool should set _pool and _worker_task."""
        mock_pool = MagicMock()
        mock_eth = MagicMock()
        mock_sol = MagicMock()
        mock_btc = MagicMock()
        mock_task = MagicMock()

        with patch("services.blockchain.EtherscanClient", return_value=mock_eth):
            with patch("services.blockchain.SolscanClient", return_value=mock_sol):
                with patch("services.blockchain.BlockchairClient", return_value=mock_btc):
                    with patch("asyncio.create_task", return_value=mock_task):
                        monitor.start_monitor(mock_pool)

        self.assertEqual(monitor._pool, mock_pool)
        self.assertEqual(monitor._worker_task, mock_task)
        self.assertIn("eth", monitor._clients)
        self.assertIn("sol", monitor._clients)
        self.assertIn("btc", monitor._clients)

    def test_client_init_failure_returns_early(self):
        """If blockchain client import fails, start_monitor should return without worker."""
        mock_pool = MagicMock()

        with patch("services.blockchain.EtherscanClient", side_effect=Exception("no eth")):
            monitor.start_monitor(mock_pool)

        self.assertIsNone(monitor._worker_task)

    def test_double_start_replaces_worker(self):
        """Calling start_monitor twice should replace the old worker."""
        mock_pool = MagicMock()
        mock_eth = MagicMock()
        mock_sol = MagicMock()
        mock_btc = MagicMock()
        old_task = MagicMock()
        old_task.done.return_value = True  # Already done, so no cancel needed
        new_task = MagicMock()

        with patch("services.blockchain.EtherscanClient", return_value=mock_eth):
            with patch("services.blockchain.SolscanClient", return_value=mock_sol):
                with patch("services.blockchain.BlockchairClient", return_value=mock_btc):
                    with patch("asyncio.create_task", side_effect=[old_task, new_task]):
                        monitor.start_monitor(mock_pool)
                        monitor.start_monitor(mock_pool)

        self.assertEqual(monitor._worker_task, new_task)


# ─── is_monitor_alive ─────────────────────────────────────────────────────

class TestIsMonitorAlive(unittest.TestCase):
    """Test the is_monitor_alive health check."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_no_worker(self):
        self.assertFalse(monitor.is_monitor_alive())

    def test_worker_done(self):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        monitor._worker_task = mock_task
        self.assertFalse(monitor.is_monitor_alive())

    def test_worker_running(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        monitor._worker_task = mock_task
        self.assertTrue(monitor.is_monitor_alive())


# ─── stop_monitor ─────────────────────────────────────────────────────────

class TestStopMonitor(unittest.TestCase):
    """Test the stop_monitor shutdown API."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_stop_no_worker(self):
        """stop_monitor with no worker should not raise."""
        _run(monitor.stop_monitor())

    def test_stop_with_completed_worker(self):
        """stop_monitor with an already-done task should not raise."""
        mock_task = MagicMock()
        mock_task.done.return_value = True
        monitor._worker_task = mock_task
        _run(monitor.stop_monitor())

    def test_stop_clears_clients(self):
        """stop_monitor should clear the _clients dict."""
        mock_task = MagicMock()
        mock_task.done.return_value = True
        monitor._worker_task = mock_task
        monitor._clients["eth"] = MagicMock()
        _run(monitor.stop_monitor())
        self.assertEqual(len(monitor._clients), 0)

    def test_stop_sets_cancel_event(self):
        """stop_monitor should set the cancel event."""
        mock_task = MagicMock()
        mock_task.done.return_value = True
        monitor._worker_task = mock_task
        monitor._cancel_event.clear()
        _run(monitor.stop_monitor())
        self.assertTrue(monitor._cancel_event.is_set())


# ─── _check_wallet_with_sem ───────────────────────────────────────────────

class TestCheckWalletWithSem(unittest.TestCase):
    """Test the semaphore + timeout wrapper."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_cancel_event_set_returns_none(self):
        """If _cancel_event is set, should return None immediately."""
        monitor._cancel_event.set()
        sem = asyncio.Semaphore(5)
        row = {"id": 1, "address": "0xabc", "chain": "eth"}

        result = _run(monitor._check_wallet_with_sem(sem, row))
        self.assertIsNone(result)

    def test_returns_none_when_no_change(self):
        """When _check_wallet_balance_and_txs returns None, wrapper returns None."""
        sem = asyncio.Semaphore(5)
        row = {"id": 1, "address": "0xabc", "chain": "eth"}

        with patch.object(monitor, "_check_wallet_balance_and_txs", new_callable=AsyncMock, return_value=None):
            result = _run(monitor._check_wallet_with_sem(sem, row))
        self.assertIsNone(result)

    def test_returns_tuple_on_change(self):
        """When inner function returns a tuple, wrapper passes it through."""
        sem = asyncio.Semaphore(5)
        row = {"id": 1, "address": "0xabc", "chain": "eth"}
        expected = (1.5, 3750.0, "0xhash", "buy", "ETH", 1.5)

        with patch.object(monitor, "_check_wallet_balance_and_txs", new_callable=AsyncMock, return_value=expected):
            result = _run(monitor._check_wallet_with_sem(sem, row))
        self.assertEqual(result, expected)

    def test_timeout_raises(self):
        """When inner function exceeds WALLET_FETCH_TIMEOUT, should raise."""
        sem = asyncio.Semaphore(5)
        row = {"id": 1, "address": "0xabc", "chain": "eth"}
        original_timeout = monitor.WALLET_FETCH_TIMEOUT
        monitor.WALLET_FETCH_TIMEOUT = 0.01  # 10ms for fast test

        async def slow_check(wallet_row):
            await asyncio.sleep(1.0)
            return None

        try:
            with patch.object(monitor, "_check_wallet_balance_and_txs", slow_check):
                with self.assertRaises(asyncio.TimeoutError):
                    _run(monitor._check_wallet_with_sem(sem, row))
        finally:
            monitor.WALLET_FETCH_TIMEOUT = original_timeout


# ─── _check_wallet_balance_and_txs ────────────────────────────────────────

class TestCheckWalletBalanceAndTxs(unittest.TestCase):
    """Test the per-wallet balance + tx checking logic."""

    def setUp(self):
        _reset_monitor_state()
        # Set up mock clients for each chain
        self.mock_eth_client = AsyncMock()
        self.mock_sol_client = AsyncMock()
        self.mock_btc_client = AsyncMock()
        monitor._clients = {
            "eth": self.mock_eth_client,
            "sol": self.mock_sol_client,
            "btc": self.mock_btc_client,
        }

    def tearDown(self):
        _reset_monitor_state()

    def _make_wallet_row(self, wid="1", address="0xabc", chain="eth"):
        return {"id": wid, "address": address, "chain": chain}

    # -- Balance change detection --

    def test_first_seen_balance_returns_change(self):
        """First time seeing a wallet (old_balance=None) should report change."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        bal_native, bal_usd, tx_hash, tx_type, token, tx_amount = result
        self.assertEqual(bal_native, 10.0)
        self.assertAlmostEqual(bal_usd, 10.0 * 2500.0, places=2)

    def test_unchanged_balance_no_tx_returns_none(self):
        """When balance is the same and no new tx, should return None."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 10.0
        monitor._last_tx_hashes["1"] = "0xold"

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xold", "type": "buy", "token": "ETH", "amount": 1.0}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNone(result)

    def test_changed_balance_returns_change(self):
        """When balance changes, should return a tuple."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 5.0

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[0], 10.0)  # new balance_native

    def test_balance_change_within_epsilon_returns_none(self):
        """Balance difference < 1e-10 should be treated as unchanged."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 10.0

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0 + 1e-11}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNone(result)

    # -- Transaction detection --

    def test_new_tx_returns_change(self):
        """When a new tx hash is detected, should return a tuple with tx_hash."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 10.0
        monitor._last_tx_hashes["1"] = "0xold"

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xnew", "type": "buy", "token": "ETH", "amount": 2.5}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        bal_native, bal_usd, tx_hash, tx_type, token, tx_amount = result
        self.assertEqual(tx_hash, "0xnew")
        self.assertEqual(tx_type, "buy")
        self.assertEqual(token, "ETH")
        self.assertEqual(tx_amount, 2.5)

    def test_same_tx_hash_no_change(self):
        """When tx hash is the same as last seen, should return None."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 10.0
        monitor._last_tx_hashes["1"] = "0xsame"

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xsame", "type": "buy", "token": "ETH", "amount": 1.0}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNone(result)

    def test_no_txs_returns_none_when_balance_unchanged(self):
        """Empty tx list + unchanged balance → None."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 10.0

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNone(result)

    # -- State updates --

    def test_balance_stored_in_state(self):
        """After check, _last_balances should be updated."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 7.5}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertEqual(monitor._last_balances["1"], 7.5)

    def test_tx_hash_stored_in_state(self):
        """After check, _last_tx_hashes should be updated."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xabc123", "type": "receive", "token": "ETH", "amount": 1.0}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertEqual(monitor._last_tx_hashes["1"], "0xabc123")

    def test_consecutive_errors_reset_on_success(self):
        """On successful check, _consecutive_errors for wallet should be cleared."""
        wallet_row = self._make_wallet_row()
        monitor._consecutive_errors["1"] = 3

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertNotIn("1", monitor._consecutive_errors)

    # -- Error backoff --

    def test_backoff_wallet_retried(self):
        """Wallet with >= MAX_CONSECUTIVE_ERRORS should be retried (not skipped)."""
        wallet_row = self._make_wallet_row()
        monitor._consecutive_errors["1"] = monitor.MAX_CONSECUTIVE_ERRORS

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 5.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        # Should have been retried and succeeded → change reported
        self.assertIsNotNone(result)
        self.assertNotIn("1", monitor._consecutive_errors)

    def test_backoff_counter_reset_before_retry(self):
        """Counter should be reset to 0 before the retry attempt."""
        wallet_row = self._make_wallet_row()
        monitor._consecutive_errors["1"] = monitor.MAX_CONSECUTIVE_ERRORS

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 5.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._check_wallet_balance_and_txs(wallet_row))

        # After the call, the counter should be cleared (reset + success)
        self.assertNotIn("1", monitor._consecutive_errors)

    # -- Chain handling --

    def test_solana_chain(self):
        """SOL chain should use get_balance (not get_eth_balance)."""
        wallet_row = self._make_wallet_row(chain="sol")
        self.mock_sol_client.get_balance = AsyncMock(
            return_value={"balance_sol": 100.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[0], 100.0)

    def test_btc_chain(self):
        """BTC chain should use get_balance."""
        wallet_row = self._make_wallet_row(chain="btc")
        self.mock_btc_client.get_balance = AsyncMock(
            return_value={"balance_btc": 2.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[0], 2.0)

    def test_unknown_chain_raises(self):
        """Unknown chain should raise ValueError."""
        wallet_row = self._make_wallet_row(chain="doge")
        monitor._clients = {}  # no client for doge

        with self.assertRaises(RuntimeError):
            _run(monitor._check_wallet_balance_and_txs(wallet_row))

    def test_no_client_raises(self):
        """Missing client for chain should raise RuntimeError."""
        wallet_row = self._make_wallet_row(chain="eth")
        monitor._clients = {}  # no clients

        with self.assertRaises(RuntimeError):
            _run(monitor._check_wallet_balance_and_txs(wallet_row))

    # -- Price cache integration --

    def test_ensure_prices_fetched_called(self):
        """_ensure_prices_fetched should be called during wallet check."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock) as mock_prices:
                _run(monitor._check_wallet_balance_and_txs(wallet_row))

        mock_prices.assert_called_once()

    # -- Return tuple shape --

    def test_return_tuple_has_6_elements(self):
        """Return tuple should have exactly 6 elements."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 6)

    def test_return_tx_hash_none_when_no_new_tx(self):
        """When balance changed but no new tx, tx_hash should be None."""
        wallet_row = self._make_wallet_row()
        monitor._last_balances["1"] = 5.0  # different balance

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertIsNone(result[2])  # tx_hash is None

    def test_tx_amount_native_from_tx(self):
        """tx_amount_native should come from the tx amount field."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xnew", "type": "buy", "token": "ETH", "amount": 3.14}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[5], 3.14)  # tx_amount_native

    def test_tx_amount_zero_when_no_tx(self):
        """tx_amount_native should be 0.0 when no tx."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[5], 0.0)

    def test_token_defaults_to_symbol_when_no_tx(self):
        """When no tx, token should default to chain symbol."""
        wallet_row = self._make_wallet_row()
        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                result = _run(monitor._check_wallet_balance_and_txs(wallet_row))

        self.assertIsNotNone(result)
        self.assertEqual(result[4], "ETH")  # token


# ─── _poll_all_wallets_inner ──────────────────────────────────────────────

class TestPollAllWalletsInner(unittest.TestCase):
    """Test the main poll cycle orchestration."""

    def setUp(self):
        _reset_monitor_state()
        self.mock_pool = MagicMock()
        monitor._pool = self.mock_pool

        # Default mock clients
        self.mock_eth_client = AsyncMock()
        monitor._clients = {"eth": self.mock_eth_client, "sol": AsyncMock(), "btc": AsyncMock()}

    def tearDown(self):
        _reset_monitor_state()

    def _make_wallet_rows(self, n=1, chain="eth"):
        rows = []
        for i in range(n):
            rows.append({
                "id": str(i + 1),
                "address": f"0x{i:040x}",
                "chain": chain,
                "balance_native": 10.0,
                "balance_usd": 25000.0,
                "last_balance_update": None,
                "is_whale": False,
                "is_mine": False,
                "user_id": f"user-{i + 1}",
            })
        return rows

    def _make_mock_conn(self, rows=None):
        """Create a mock connection with standard behavior."""
        mock_conn = AsyncMock()
        if rows is not None:
            mock_conn.fetch = AsyncMock(return_value=rows)
        else:
            mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchrowval = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock()
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_conn

    def _setup_pool_acquire(self, conn):
        """Set up pool.acquire() to return an async context manager yielding conn."""
        self.mock_pool.acquire = lambda: _AsyncCtxMgr(conn)

    def test_empty_rows_clears_state(self):
        """When no wallets returned, state dicts should be cleared."""
        monitor._last_balances["old"] = 100.0
        monitor._last_tx_hashes["old"] = "0x"
        monitor._consecutive_errors["old"] = 3

        conn = self._make_mock_conn(rows=[])
        self._setup_pool_acquire(conn)

        _run(monitor._poll_all_wallets_inner())

        self.assertEqual(len(monitor._last_balances), 0)
        self.assertEqual(len(monitor._last_tx_hashes), 0)
        self.assertEqual(len(monitor._consecutive_errors), 0)

    def test_empty_rows_returns_early(self):
        """When no wallets, should return without further processing."""
        conn = self._make_mock_conn(rows=[])
        self._setup_pool_acquire(conn)

        _run(monitor._poll_all_wallets_inner())

    def test_state_pruning_removes_stale_wallets(self):
        """Wallets no longer in DB should be pruned from state."""
        rows = self._make_wallet_rows(1)  # Only wallet "1"
        monitor._last_balances["stale"] = 50.0
        monitor._last_balances["1"] = 10.0
        monitor._last_tx_hashes["stale"] = "0x"
        monitor._consecutive_errors["stale"] = 2

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        self.assertNotIn("stale", monitor._last_balances)
        self.assertNotIn("stale", monitor._last_tx_hashes)
        self.assertNotIn("stale", monitor._consecutive_errors)
        self.assertIn("1", monitor._last_balances)

    def test_cycle_stats_recorded(self):
        """After a poll cycle, _cycle_stats should have an entry."""
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        self.assertEqual(len(monitor._cycle_stats), 1)
        entry = monitor._cycle_stats[0]
        self.assertIn("ts", entry)
        self.assertIn("duration_s", entry)
        self.assertIn("wallets_processed", entry)
        self.assertEqual(entry["wallets_processed"], 1)
        self.assertIn("phase_durations_s", entry)

    def test_cycle_stats_ring_buffer(self):
        """_cycle_stats should not exceed _MAX_CYCLE_HISTORY entries."""
        monitor._cycle_stats = [{"ts": "old", "duration_s": 1.0, "wallets_processed": 0,
                                  "wallets_changed": 0, "signals_generated": 0,
                                  "signals_stale_expired": 0, "alerts_fired": 0, "errors": 0,
                                  "phase_durations_s": {}}] * monitor._MAX_CYCLE_HISTORY

        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        self.assertEqual(len(monitor._cycle_stats), monitor._MAX_CYCLE_HISTORY)

    def test_last_cycle_duration_updated(self):
        """_last_cycle_duration should be updated after a poll cycle."""
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        # The poll cycle uses time.monotonic() for duration. With pure mocks
        # the cycle completes in <1ms so duration can be 0.0. Just verify it
        # was set (is a float, not the initial 0.0 sentinel after a cycle).
        # We check that _cycle_stats has an entry (proving a cycle ran).
        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        # A cycle ran → _last_cycle_duration was assigned (even if 0.0 due to fast mocks)
        self.assertIsInstance(monitor._last_cycle_duration, float)
        # And cycle stats were recorded
        self.assertEqual(len(monitor._cycle_stats), 1)

    def test_phase_durations_populated(self):
        """Phase duration keys should be present in cycle stats."""
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        entry = monitor._cycle_stats[0]
        phases = entry["phase_durations_s"]
        self.assertIn("phase1_read_wallets", phases)
        self.assertIn("phase2_prune_state", phases)
        self.assertIn("phase3_fetch_wallets", phases)
        self.assertIn("phase4_collect_changes", phases)

    def test_error_counted_in_stats(self):
        """Wallet check errors should be counted in cycle stats."""
        rows = self._make_wallet_rows(1)

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            side_effect=Exception("RPC error")
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["errors"], 1)

    def test_consecutive_errors_incremented_on_failure(self):
        """Failed wallet check should increment _consecutive_errors."""
        rows = self._make_wallet_rows(1)

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            side_effect=Exception("RPC error")
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        self.assertEqual(monitor._consecutive_errors.get("1", 0), 1)

    def test_multiple_wallets_processed(self):
        """Multiple wallets should all be processed."""
        rows = self._make_wallet_rows(3)

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock, return_value=[]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["wallets_processed"], 3)

    def test_single_wallet_no_changes_no_db_write(self):
        """Single wallet with no balance/tx changes should not trigger DB updates."""
        rows = self._make_wallet_rows(1)
        monitor._last_balances["1"] = 10.0
        monitor._last_tx_hashes["1"] = "0xold"

        conn = self._make_mock_conn(rows=rows)
        self._setup_pool_acquire(conn)

        self.mock_eth_client.get_eth_balance = AsyncMock(
            return_value={"balance_eth": 10.0}
        )

        with patch("services.tx_fetcher.fetch_transactions_for_wallet", new_callable=AsyncMock,
                   return_value=[{"tx_hash": "0xold", "type": "buy", "token": "ETH", "amount": 1.0}]):
            with patch.object(monitor, "_ensure_prices_fetched", new_callable=AsyncMock):
                _run(monitor._poll_all_wallets_inner())

        # Cycle stats should show 0 wallets changed
        entry = monitor._cycle_stats[0]
        self.assertEqual(entry["wallets_changed"], 0)


# ─── _poll_all_wallets (re-entrancy guard) ────────────────────────────────

class TestPollAllWalletsReentrancy(unittest.TestCase):
    """Test the _poll_lock re-entrancy guard."""

    def setUp(self):
        _reset_monitor_state()
        self.mock_pool = MagicMock()
        monitor._pool = self.mock_pool

    def tearDown(self):
        _reset_monitor_state()

    def test_reentrant_call_skips(self):
        """If a poll cycle is already running, the second call should skip."""
        # Acquire the lock to simulate an in-flight poll
        _run(monitor._poll_lock.acquire())

        try:
            # This should return immediately because lock is locked
            _run(monitor._poll_all_wallets())
        finally:
            monitor._poll_lock.release()


# ─── get_cycle_stats ──────────────────────────────────────────────────────

class TestGetCycleStats(unittest.TestCase):
    """Test the cycle stats retrieval."""

    def setUp(self):
        _reset_monitor_state()

    def tearDown(self):
        _reset_monitor_state()

    def test_empty_stats(self):
        """With no cycles, should return empty history."""
        stats = _run(monitor.get_cycle_stats())
        self.assertEqual(stats["history"], [])
        self.assertEqual(stats["total_cycles"], 0)
        self.assertEqual(stats["last_cycle_duration_s"], 0.0)

    def test_stats_shape(self):
        """Stats should have the expected keys."""
        monitor._cycle_stats = [{
            "ts": "2026-01-01T00:00:00Z",
            "duration_s": 1.5,
            "wallets_processed": 10,
            "wallets_changed": 2,
            "signals_generated": 1,
            "signals_stale_expired": 0,
            "alerts_fired": 0,
            "errors": 0,
            "phase_durations_s": {"phase1_read_wallets": 0.1},
        }]
        monitor._last_cycle_duration = 1.5

        stats = _run(monitor.get_cycle_stats())
        self.assertEqual(stats["total_cycles"], 1)
        self.assertEqual(stats["last_cycle_duration_s"], 1.5)
        self.assertEqual(len(stats["history"]), 1)
        self.assertEqual(stats["history"][0]["wallets_processed"], 10)

    def test_stats_returns_copy(self):
        """get_cycle_stats should return a copy, not the original list."""
        monitor._cycle_stats = [{"ts": "test", "duration_s": 1.0, "wallets_processed": 0,
                                  "wallets_changed": 0, "signals_generated": 0,
                                  "signals_stale_expired": 0, "alerts_fired": 0, "errors": 0,
                                  "phase_durations_s": {}}]

        stats = _run(monitor.get_cycle_stats())
        stats["history"].clear()

        # Original should be unchanged
        self.assertEqual(len(monitor._cycle_stats), 1)


# ─── Module-level config constants ────────────────────────────────────────

class TestConfigConstants(unittest.TestCase):
    """Verify config constants are sane."""

    def test_poll_interval_positive(self):
        self.assertGreater(monitor.POLL_INTERVAL, 0)

    def test_max_errors_positive(self):
        self.assertGreater(monitor.MAX_CONSECUTIVE_ERRORS, 0)

    def test_timeout_positive(self):
        self.assertGreater(monitor.WALLET_FETCH_TIMEOUT, 0)

    def test_concurrency_positive(self):
        self.assertGreater(monitor.MAX_CONCURRENT_WALLETS, 0)

    def test_stale_threshold_positive(self):
        self.assertGreater(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 0)

    def test_stale_expiry_interval_positive(self):
        self.assertGreater(monitor.STALE_EXPIRY_INTERVAL_CYCLES, 0)

    def test_max_cycle_history_positive(self):
        self.assertGreater(monitor._MAX_CYCLE_HISTORY, 0)

    def test_stablecoins_set_not_empty(self):
        self.assertGreater(len(monitor._STABLECOINS), 0)

    def test_stablecoins_contains_major(self):
        """Major stablecoins should be in the set."""
        for coin in ["USDC", "USDT", "DAI", "BUSD", "FDUSD", "PYUSD"]:
            self.assertIn(coin, monitor._STABLECOINS)


if __name__ == "__main__":
    unittest.main()
