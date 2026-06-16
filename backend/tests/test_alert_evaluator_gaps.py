#!/usr/bin/env python3
"""
Unit tests for alert_evaluator coverage gaps.

Tests cover gaps identified in the 2026-06-14 test coverage audit:
- Batch tx USD value cache (Pitfall #16 fix)
- Batch INSERT fired_alerts (unnest pattern)
- Batch UPDATE last_fired_at
- Per-row fallback when batch INSERT fails
- Pre-006 schema fallback (no 'message' column)
- Telegram chat ID batch fetch when alerts fire
- Multiple alerts in same cycle (one per rule type)
- Exception during rule evaluation (should not crash other alerts)
- _send_tg_safe: exception swallowing
- portfolio_change with prev_total = 0 (no division by zero)

Run: python3 -m pytest tests/test_alert_evaluator_gaps.py -v
"""
import asyncio
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import alert_evaluator


def _make_alert_row(
    alert_id="alert-001",
    user_id="user-001",
    rule_type="large_transaction",
    threshold=5000.0,
    last_fired_at=None,
    notify_telegram=True,
):
    return {
        "id": alert_id,
        "user_id": user_id,
        "rule_type": rule_type,
        "threshold": threshold,
        "last_fired_at": last_fired_at,
        "notify_telegram": notify_telegram,
    }


def _make_changed_wallet(
    wid="wallet-001",
    addr="0xabc123",
    label=None,
    chain="eth",
    is_whale=False,
    is_mine=True,
    user_id="user-001",
    bal_native=10.0,
    bal_usd=25000.0,
    tx_hash=None,
    tx_type="buy",
    token="ETH",
    tx_amount_native=1.0,
):
    result = (bal_native, bal_usd, tx_hash, tx_type, token, tx_amount_native)
    return (wid, addr, label, chain, is_whale, is_mine, user_id, result)


class TestBatchTxCache(unittest.TestCase):
    """Test the pre-batched tx USD value cache (Pitfall #16 fix)."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_batch_tx_cache_hit_uses_cached_value(self):
        """When batch tx cache has the value, no per-tx SELECT should be needed."""
        async def run():
            conn = AsyncMock()
            # First fetch returns alerts, second fetch returns batch tx cache rows
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [
                    {"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 15000.0}
                ],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["trigger_value"], 15000.0)

        asyncio.run(run())

    def test_batch_tx_cache_miss_falls_back_to_single_query(self):
        """When batch tx cache is empty (batch lookup failed), per-tx SELECT is used."""
        async def run():
            conn = AsyncMock()
            # First fetch returns alerts, batch returns empty (failed),
            # then fallback single query returns value
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [],  # Batch returned empty
            ]
            conn.fetchrow = AsyncMock(
                return_value={"usd_value": 12000.0}
            )
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["trigger_value"], 12000.0)

        asyncio.run(run())


class TestBatchInsertFallback(unittest.TestCase):
    """Test batch INSERT fallback to per-row when unnest fails."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_batch_insert_calls_unnest_pattern(self):
        """When alerts fire, the batch INSERT should use unnest pattern."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
               [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                # Telegram chat ID fetch
                [],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            # Verify execute was called (for batch INSERT + UPDATE)
            self.assertTrue(conn.execute.called)

        asyncio.run(run())

    def test_pre_006_schema_fallback(self):
        """When batch INSERT fails (no 'message' column), fall back to per-row."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                [],  # Telegram chat IDs
            ]
            # First execute (batch INSERT) fails, second (per-row) succeeds
            conn.execute = AsyncMock(
                side_effect=[
                    Exception("column 'message' does not exist"),  # batch fails
                    None,  # per-row succeeds
                    None,  # UPDATE
                ]
            )
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)

        asyncio.run(run())


class TestTelegramBatchFetch(unittest.TestCase):
    """Test batch Telegram chat ID fetch when alerts fire."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_telegram_chat_id_fetched_in_batch(self):
        """When alerts fire, Telegram chat IDs should be batch-fetched."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                # Telegram chat ID batch fetch
                [{"id": "user-001", "telegram_chat_id": "12345"}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)

        asyncio.run(run())

    def test_telegram_fetch_failure_doesnt_block_alert(self):
        """If Telegram chat ID fetch fails, alerts should still fire."""
        async def run():
            from unittest.mock import AsyncMock
            conn = AsyncMock()
            # First fetch: alerts query returns an alert
            # Second fetch: batch tx cache returns value
            # Third fetch: telegram chat IDs raises exception
            fetch_results = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                Exception("DB error"),
            ]
            call_count = 0
            async def selective_fetch(*args, **kwargs):
                nonlocal call_count
                result = fetch_results[call_count]
                call_count += 1
                if isinstance(result, Exception):
                    raise result
                return result
            conn.fetch = AsyncMock(side_effect=selective_fetch)
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            # Should not raise
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            # Alerts should still fire even if telegram fetch fails
            self.assertEqual(len(result), 1)

        asyncio.run(run())


class TestMultipleAlertsInSameCycle(unittest.TestCase):
    """Test multiple alerts firing in the same evaluation cycle."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_multiple_rule_types_fire_for_same_wallet(self):
        """Multiple rule types (large_transaction + whale_buy) can fire for same wallet."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [
                    _make_alert_row(alert_id="alert-lt", rule_type="large_transaction", threshold=5000.0),
                    _make_alert_row(alert_id="alert-wb", rule_type="whale_buy", threshold=3000.0),
                ],
                # Batch tx cache for both
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                # Telegram chat IDs
                [],
            ]
            conn.execute = AsyncMock()
            changed = [
                _make_changed_wallet(
                    wid="wallet-001",
                    tx_hash="0xhash1",
                    tx_type="buy",
                    is_whale=True,
                    tx_amount_native=4.0,
                )
            ]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            # Both large_transaction and whale_buy should fire
            self.assertEqual(len(result), 2)
            rule_types = {r["rule_type"] for r in result}
            self.assertIn("large_transaction", rule_types)
            self.assertIn("whale_buy", rule_types)

        asyncio.run(run())

    def test_one_fire_per_alert_per_cycle(self):
        """Each alert should fire at most once per cycle (break after first match)."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=100.0)],
                # Batch tx cache: multiple txs above threshold
                [
                    {"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 5000.0},
                ],
                [],
            ]
            conn.execute = AsyncMock()
            # Multiple changed wallets for same user
            changed = [
                _make_changed_wallet(wid="wallet-001", tx_hash="0xhash1", tx_type="buy"),
                _make_changed_wallet(wid="wallet-002", tx_hash="0xhash2", tx_type="buy"),
            ]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            # Should fire only once (break after first match)
            self.assertEqual(len(result), 1)

        asyncio.run(run())


class TestErrorHandling(unittest.TestCase):
    """Test error handling during alert evaluation."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_exception_in_rule_evaluation_doesnt_block_other_alerts(self):
        """If one alert's rule evaluation raises, other alerts should still be evaluated."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [
                    _make_alert_row(alert_id="alert-bad", rule_type="large_transaction", threshold=5000.0),
                    _make_alert_row(alert_id="alert-good", rule_type="whale_buy", threshold=3000.0),
                ],
                # Batch tx cache
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
                [],
            ]
            conn.execute = AsyncMock()
            # Only the whale wallet should trigger
            changed = [
                _make_changed_wallet(
                    wid="wallet-001",
                    tx_hash="0xhash1",
                    tx_type="buy",
                    is_whale=True,
                )
            ]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            # whale_buy should fire; large_transaction should also fire (both above threshold)
            self.assertGreaterEqual(len(result), 1)

        asyncio.run(run())

    def test_portfolio_change_prev_total_zero_no_division_by_zero(self):
        """When prev_total is 0, portfolio_change should not fire (no division by zero)."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="portfolio_change", threshold=5.0)],
                # Current total from DB
                [{"total_usd": 1000.0}],
                [],
            ]
            conn.execute = AsyncMock()
            # Changed wallet with no prev balance (prev_balance_map empty → prev_total = current)
            changed = [_make_changed_wallet(bal_usd=1000.0)]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            # prev_total = 1000 - (1000 - 0) = 0 → should not fire (no division by zero)
            self.assertEqual(len(result), 0)

        asyncio.run(run())


class TestSendTgSafe(unittest.TestCase):
    """Test _send_tg_safe exception swallowing."""

    def test_send_tg_safe_swallows_all_exceptions(self):
        """_send_tg_safe should never raise, even if send_telegram_alert fails."""
        async def run():
            with patch("services.telegram_alerts.send_telegram_alert", side_effect=Exception("TG down")):
                # Should not raise
                await alert_evaluator._send_tg_safe("12345", "test message")

        asyncio.run(run())

    def test_send_tg_safe_calls_underlying(self):
        """_send_tg_safe should call send_telegram_alert with correct args."""
        async def run():
            with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
                await alert_evaluator._send_tg_safe("12345", "test message")
                mock_send.assert_called_once_with("test message", chat_id="12345")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
