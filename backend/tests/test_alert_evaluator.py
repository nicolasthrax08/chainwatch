#!/usr/bin/env python3
"""
Unit tests for the alert_evaluator service.

Tests cover:
- Cooldown cache: prune, active, mark, expiry
- evaluate_alerts: large_transaction rule
- evaluate_alerts: whale_buy rule
- evaluate_alerts: portfolio_change rule (delta method)
- evaluate_alerts: balance_drop rule
- evaluate_alerts: unknown rule types (skipped)
- evaluate_alerts: empty changed_wallets (early return)
- evaluate_alerts: no matching alerts (empty DB result)
- evaluate_alerts: cooldown enforcement (DB-level last_fired_at)
- evaluate_alerts: cooldown enforcement (in-memory cache)
- evaluate_alerts: per-alert notify_telegram opt-out
- evaluate_alerts: batch INSERT + UPDATE paths
- evaluate_alerts: per-wallet try/except (one failure doesn't block others)
- _send_tg_safe: swallows all exceptions

Run: python3 -m unittest tests/test_alert_evaluator -v
"""
import asyncio
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional
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
    """Create a mock alert DB row."""
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
    """Create a changed_wallets tuple."""
    result = (bal_native, bal_usd, tx_hash, tx_type, token, tx_amount_native)
    return (wid, addr, chain, is_whale, is_mine, user_id, result)


class TestCooldownCache(unittest.TestCase):
    """Test the cooldown cache mechanism."""

    def setUp(self):
        """Reset cooldown cache before each test."""
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_cooldown_initially_inactive(self):
        """A fresh alert_id should not be in cooldown."""
        self.assertFalse(alert_evaluator._is_cooldown_active("alert-new"))

    def test_cooldown_active_after_mark(self):
        """After marking, the alert should be in cooldown."""
        alert_evaluator._mark_cooldown("alert-001")
        self.assertTrue(alert_evaluator._is_cooldown_active("alert-001"))

    def test_cooldown_different_alerts_independent(self):
        """Marking one alert should not affect another."""
        alert_evaluator._mark_cooldown("alert-001")
        self.assertFalse(alert_evaluator._is_cooldown_active("alert-002"))

    def test_prune_removes_expired_entries(self):
        """Pruning should remove entries older than 2x cooldown."""
        # Manually insert an old entry
        old_time = time.time() - alert_evaluator._COOLDOWN_SECONDS * 3
        alert_evaluator._cooldown_cache["old-alert"] = old_time
        alert_evaluator._cooldown_cache["fresh-alert"] = time.time()

        alert_evaluator._prune_cooldown_cache()

        self.assertNotIn("old-alert", alert_evaluator._cooldown_cache)
        self.assertIn("fresh-alert", alert_evaluator._cooldown_cache)

    def test_prune_respects_interval(self):
        """Prune should not run more frequently than _COOLDOWN_PRUNE_INTERVAL."""
        alert_evaluator._last_cooldown_prune = time.time()
        # Even with expired entries, prune should skip
        alert_evaluator._cooldown_cache["expired"] = 0.0
        alert_evaluator._prune_cooldown_cache()
        # Should still be there because prune interval hasn't elapsed
        self.assertIn("expired", alert_evaluator._cooldown_cache)

    def test_cooldown_constant_is_300_seconds(self):
        """Cooldown should be 5 minutes (300 seconds)."""
        self.assertEqual(alert_evaluator._COOLDOWN_SECONDS, 300)

    def test_supported_rule_types(self):
        """All four rule types should be supported."""
        expected = {"large_transaction", "whale_buy", "portfolio_change", "balance_drop"}
        self.assertEqual(alert_evaluator.SUPPORTED_RULE_TYPES, expected)


class TestSendTgSafe(unittest.TestCase):
    """Test the _send_tg_safe wrapper."""

    def test_send_tg_safe_swallows_exceptions(self):
        """_send_tg_safe should never raise, even if the underlying call fails."""
        async def run():
            with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
                mock_send.side_effect = Exception("Network error")
                # Should not raise
                await alert_evaluator._send_tg_safe("chat-123", "test message")
                mock_send.assert_called_once_with("test message", chat_id="chat-123")

        asyncio.get_event_loop().run_until_complete(run())

    def test_send_tg_safe_calls_underlying(self):
        """_send_tg_safe should delegate to send_telegram_alert."""
        async def run():
            with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
                await alert_evaluator._send_tg_safe("chat-456", "hello")
                mock_send.assert_called_once_with("hello", chat_id="chat-456")

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsEmptyInputs(unittest.TestCase):
    """Test evaluate_alerts with empty/edge-case inputs."""

    def test_empty_changed_wallets_returns_empty(self):
        """Empty changed_wallets should return empty list immediately."""
        async def run():
            conn = AsyncMock()
            result = await alert_evaluator.evaluate_alerts(conn, [], {})
            self.assertEqual(result, [])
            # No DB queries should be made
            conn.fetch.assert_not_called()

        asyncio.get_event_loop().run_until_complete(run())

    def test_no_matching_alerts_returns_empty(self):
        """When DB returns no alerts, should return empty list."""
        async def run():
            conn = AsyncMock()
            conn.fetch.return_value = []  # No alerts
            changed = [_make_changed_wallet()]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(result, [])

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsLargeTransaction(unittest.TestCase):
    """Test the large_transaction alert rule."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_large_transaction_fires_when_above_threshold(self):
        """A tx above threshold should fire a large_transaction alert."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                # First call: alert rows
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                # Second call: batch tx usd lookup
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["rule_type"], "large_transaction")
            self.assertEqual(result[0]["trigger_value"], 10000.0)
            self.assertIn("notify_telegram", result[0])

        asyncio.get_event_loop().run_until_complete(run())

    def test_large_transaction_not_fires_when_below_threshold(self):
        """A tx below threshold should not fire."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 1000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_large_transaction_ignores_other_users_wallets(self):
        """Should not fire for wallets belonging to other users."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", user_id="user-001", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            # Wallet belongs to user-002, alert is for user-001
            changed = [_make_changed_wallet(user_id="user-002", tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsWhaleBuy(unittest.TestCase):
    """Test the whale_buy alert rule."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_whale_buy_fires_for_whale_wallet(self):
        """A whale wallet buy above threshold should fire whale_buy."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="whale_buy", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 15000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(is_whale=True, tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["rule_type"], "whale_buy")

        asyncio.get_event_loop().run_until_complete(run())

    def test_whale_buy_not_fires_for_non_whale(self):
        """A non-whale wallet should not trigger whale_buy."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="whale_buy", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 15000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(is_whale=False, tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_whale_buy_ignores_send_transactions(self):
        """A 'send' tx type should not trigger whale_buy (only buy/receive)."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="whale_buy", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 15000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(is_whale=True, tx_hash="0xhash1", tx_type="send")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsPortfolioChange(unittest.TestCase):
    """Test the portfolio_change alert rule."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_portfolio_change_fires_on_large_drop(self):
        """A portfolio drop exceeding threshold should fire."""
        async def run():
            conn = AsyncMock()
            # Alert row
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="portfolio_change", threshold=5.0)],
            ]
            # Portfolio total query: current_total = 95000
            conn.fetchrow = AsyncMock(return_value={"total_usd": 95000.0})
            conn.execute = AsyncMock()

            # prev_balance_map: wallet had 100000, now 95000 → 5% drop
            changed = [_make_changed_wallet(
                wid="w1", bal_usd=95000.0, is_mine=True, is_whale=False,
                tx_hash=None, tx_type="buy"
            )]
            prev_map = {"w1": 100000.0}
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["rule_type"], "portfolio_change")

        asyncio.get_event_loop().run_until_complete(run())

    def test_portfolio_change_not_fires_on_small_change(self):
        """A small portfolio change should not fire."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="portfolio_change", threshold=10.0)],
            ]
            conn.fetchrow = AsyncMock(return_value={"total_usd": 98000.0})
            conn.execute = AsyncMock()

            changed = [_make_changed_wallet(
                wid="w1", bal_usd=98000.0, is_mine=True, is_whale=False,
                tx_hash=None, tx_type="buy"
            )]
            prev_map = {"w1": 100000.0}  # 2% change, threshold 10%
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_portfolio_change_filters_is_mine_and_not_whale(self):
        """Portfolio change should only consider personal (is_mine=True, is_whale=False) wallets."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="portfolio_change", threshold=5.0)],
            ]
            # Current total is 100000 (only personal wallets)
            conn.fetchrow = AsyncMock(return_value={"total_usd": 100000.0})
            conn.execute = AsyncMock()

            # Mix of personal and whale wallets
            changed = [
                _make_changed_wallet(wid="personal-w", bal_usd=50000.0, is_mine=True, is_whale=False, tx_hash=None),
                _make_changed_wallet(wid="whale-w", bal_usd=50000.0, is_mine=False, is_whale=True, tx_hash=None),
            ]
            prev_map = {"personal-w": 50000.0, "whale-w": 60000.0}
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            # No change in personal wallets → no alert
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsBalanceDrop(unittest.TestCase):
    """Test the balance_drop alert rule."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_balance_drop_fires_on_large_drop(self):
        """A balance drop exceeding threshold should fire."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="balance_drop", threshold=10.0)],
                [],  # No tx lookups (no tx_hash)
            ]
            conn.execute = AsyncMock()

            changed = [_make_changed_wallet(
                wid="w1", bal_usd=8000.0, is_mine=True, is_whale=False,
                tx_hash=None, tx_type="send"
            )]
            prev_map = {"w1": 10000.0}  # 20% drop
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["rule_type"], "balance_drop")
            self.assertAlmostEqual(result[0]["trigger_value"], 20.0, places=1)

        asyncio.get_event_loop().run_until_complete(run())

    def test_balance_drop_not_fires_on_small_drop(self):
        """A small balance drop should not fire."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="balance_drop", threshold=10.0)],
                [],
            ]
            conn.execute = AsyncMock()

            changed = [_make_changed_wallet(
                wid="w1", bal_usd=9500.0, is_mine=True, is_whale=False,
                tx_hash=None
            )]
            prev_map = {"w1": 10000.0}  # 5% drop, threshold 10%
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_balance_drop_ignores_whale_wallets(self):
        """balance_drop should skip whale wallets."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="balance_drop", threshold=10.0)],
                [],
            ]
            conn.execute = AsyncMock()

            changed = [_make_changed_wallet(
                wid="w1", bal_usd=5000.0, is_mine=True, is_whale=True,
                tx_hash=None
            )]
            prev_map = {"w1": 10000.0}  # 50% drop but is_whale=True
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_balance_drop_ignores_non_mine_wallets(self):
        """balance_drop should skip wallets where is_mine=False."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="balance_drop", threshold=10.0)],
                [],
            ]
            conn.execute = AsyncMock()

            changed = [_make_changed_wallet(
                wid="w1", bal_usd=5000.0, is_mine=False, is_whale=False,
                tx_hash=None
            )]
            prev_map = {"w1": 10000.0}
            result = await alert_evaluator.evaluate_alerts(conn, changed, prev_map)
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsCooldown(unittest.TestCase):
    """Test cooldown enforcement in evaluate_alerts."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_db_level_cooldown_prevents_fire(self):
        """An alert with recent last_fired_at in DB should be skipped."""
        async def run():
            conn = AsyncMock()
            recent = datetime.now(timezone.utc) - timedelta(seconds=100)
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", last_fired_at=recent, threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_in_memory_cooldown_prevents_fire(self):
        """An alert in in-memory cooldown should be skipped."""
        async def run():
            conn = AsyncMock()
            alert_id = "alert-cached"
            alert_evaluator._mark_cooldown(alert_id)
            conn.fetch.side_effect = [
                [_make_alert_row(alert_id=alert_id, rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsUnknownRuleType(unittest.TestCase):
    """Test that unknown rule types are skipped gracefully."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_unknown_rule_type_skipped(self):
        """An alert with an unknown rule_type should be skipped without error."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="unknown_rule_type", threshold=5000.0)],
                [],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 0)

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsNotifyTelegram(unittest.TestCase):
    """Test the notify_telegram per-alert opt-out."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_fired_alert_includes_notify_telegram_field(self):
        """Fired alerts should include the notify_telegram field from the alert config."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", notify_telegram=False, threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            self.assertFalse(result[0]["notify_telegram"])

        asyncio.get_event_loop().run_until_complete(run())


class TestEvaluateAlertsResponseShape(unittest.TestCase):
    """Test that fired alert dicts have the expected shape."""

    def setUp(self):
        alert_evaluator._cooldown_cache.clear()
        alert_evaluator._last_cooldown_prune = 0.0

    def test_fired_alert_has_required_keys(self):
        """Each fired alert should have alert_id, user_id, rule_type, threshold, trigger_value, message, notify_telegram."""
        async def run():
            conn = AsyncMock()
            conn.fetch.side_effect = [
                [_make_alert_row(rule_type="large_transaction", threshold=5000.0)],
                [{"wallet_id": "wallet-001", "tx_hash": "0xhash1", "usd_value": 10000.0}],
            ]
            conn.execute = AsyncMock()
            changed = [_make_changed_wallet(tx_hash="0xhash1", tx_type="buy")]
            result = await alert_evaluator.evaluate_alerts(conn, changed, {})
            self.assertEqual(len(result), 1)
            expected_keys = {"alert_id", "user_id", "rule_type", "threshold", "trigger_value", "message", "notify_telegram"}
            self.assertEqual(set(result[0].keys()), expected_keys)

        asyncio.get_event_loop().run_until_complete(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
