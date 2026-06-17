#!/usr/bin/env python3
"""
Tests for signal outcome tracking endpoints.

Covers:
- POST /api/signals/{signal_id}/outcome — record user outcome
- GET /api/signals/outcome-stats — aggregate outcome statistics

Run: python3 -m pytest backend/tests/test_signal_outcome.py -v
"""
import os
import sys
import uuid
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend is on the path and JWT_SECRET is set before importing main
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("JWT_SECRET", "test-secret-for-outcome-tests")

from main import app, create_jwt, acquire_db


# ─── Helpers ──────────────────────────────────────────────────────────


class _AsyncCtxMgr:
    """Helper: wraps a mock as an async context manager."""

    def __init__(self, mock_obj):
        self._mock = mock_obj

    async def __aenter__(self):
        return self._mock

    async def __aexit__(self, *args):
        return False


def _make_signal_row(**overrides):
    """Create a mock asyncpg row dict for a copy_trade_signals record."""
    row = {
        "id": uuid.uuid4(),
        "token_symbol": "ETH",
        "action": "buy",
        "amount_usd": 5000.0,
        "confidence_score": 0.75,
        "confidence_final": 0.65,
        "score_at_generation": 0.55,
        "status": "pending",
        "user_pnl_usd": None,
        "user_outcome": None,
        "user_notes": None,
        "created_at": MagicMock(isoformat=MagicMock(return_value="2026-01-15T12:00:00+00:00")),
        "reviewed_at": None,
    }
    row.update(overrides)
    return row


def _make_outcome_stats_row(**overrides):
    """Create a mock asyncpg row dict for outcome stats aggregation."""
    row = {
        "total_reviewed": 10,
        "profit_count": 6,
        "loss_count": 3,
        "breakeven_count": 1,
        "skipped_count": 0,
        "total_pnl_usd": 15000.00,
        "avg_pnl_usd": 1500.00,
        "avg_profit_usd": 3000.00,
        "avg_loss_usd": -1000.00,
    }
    row.update(overrides)
    return row


def _make_tier_row(tier, reviewed=5, total_pnl=5000, avg_pnl=1000, profit_count=3):
    return {
        "tier": tier,
        "tier_reviewed": reviewed,
        "tier_total_pnl": total_pnl,
        "tier_avg_pnl": avg_pnl,
        "tier_profit_count": profit_count,
    }


def _make_conf_row(conf_tier, reviewed=5, total_pnl=5000, avg_pnl=1000):
    return {
        "conf_tier": conf_tier,
        "conf_reviewed": reviewed,
        "conf_total_pnl": total_pnl,
        "conf_avg_pnl": avg_pnl,
    }


# ─── Test: POST /api/signals/{signal_id}/outcome ──────────────────────


class TestRecordSignalOutcome(unittest.TestCase):
    """Tests for POST /api/signals/{signal_id}/outcome.

    Uses the same DB pool mocking pattern as TestSignalStatsEndpoint
    in test_signal_stats.py: patch main.db_pool directly so that
    acquire_db() (which checks `if db_pool is None`) works without
    a real database.
    """

    def setUp(self):
        import main as main_mod
        self._main_mod = main_mod
        from fastapi.testclient import TestClient
        self.client = TestClient(main_mod.app)
        self.user_id = str(uuid.uuid4())
        self.wallet = "0x" + "a" * 40
        # Create a JWT with uid claim so get_current_user returns
        # without needing a DB lookup (Pitfall #21 optimization)
        self.token = main_mod.create_jwt(self.wallet, self.user_id)

    def _mock_db_pool(self, side_effects):
        """Replace main.db_pool with a mock that returns the given data.

        Args:
            side_effects: list of values to return from successive
                         conn.fetchrow() calls.
        """
        main_mod = self._main_mod
        mock_conn = AsyncMock()
        # Use side_effect to return different values for successive calls
        mock_conn.fetchrow = AsyncMock(side_effect=list(side_effects))

        mock_pool = MagicMock()
        mock_pool.acquire = lambda: _AsyncCtxMgr(mock_conn)

        self._pool_patcher = patch("main.db_pool", mock_pool)
        self._pool_patcher.start()

    def tearDown(self):
        if hasattr(self, "_pool_patcher"):
            try:
                self._pool_patcher.stop()
            except RuntimeError:
                pass
        self._main_mod.db_pool = None

    def test_record_outcome_success(self):
        """Recording a valid outcome should return 200 with signal data."""
        signal_id = str(uuid.uuid4())
        self._mock_db_pool([
            {"id": uuid.UUID(signal_id)},  # ownership check
            _make_signal_row(
                id=uuid.UUID(signal_id),
                user_pnl_usd=1500.00,
                user_outcome="profit",
                user_notes="Great call!",
                reviewed_at=MagicMock(isoformat=MagicMock(return_value="2026-01-15T13:00:00+00:00")),
            ),
        ])

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_pnl_usd": 1500.00, "user_outcome": "profit", "user_notes": "Great call!"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()["signal"]
        self.assertEqual(data["user_pnl_usd"], 1500.00)
        self.assertEqual(data["user_outcome"], "profit")
        self.assertEqual(data["user_notes"], "Great call!")
        self.assertIsNotNone(data["reviewed_at"])

    def test_record_outcome_only_pnl(self):
        """Recording only P&L (no outcome) should succeed."""
        signal_id = str(uuid.uuid4())
        self._mock_db_pool([
            {"id": uuid.UUID(signal_id)},
            _make_signal_row(
                id=uuid.UUID(signal_id),
                user_pnl_usd=-500.00,
                user_outcome=None,
                user_notes=None,
                reviewed_at=MagicMock(isoformat=MagicMock(return_value="2026-01-15T13:00:00+00:00")),
            ),
        ])

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_pnl_usd": -500.00},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()["signal"]
        self.assertEqual(data["user_pnl_usd"], -500.00)
        self.assertIsNone(data["user_outcome"])

    def test_record_outcome_only_outcome(self):
        """Recording only outcome (no P&L) should succeed."""
        signal_id = str(uuid.uuid4())
        self._mock_db_pool([
            {"id": uuid.UUID(signal_id)},
            _make_signal_row(
                id=uuid.UUID(signal_id),
                user_pnl_usd=None,
                user_outcome="skipped",
                user_notes=None,
                reviewed_at=MagicMock(isoformat=MagicMock(return_value="2026-01-15T13:00:00+00:00")),
            ),
        ])

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_outcome": "skipped"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()["signal"]
        self.assertEqual(data["user_outcome"], "skipped")
        self.assertIsNone(data["user_pnl_usd"])

    def test_record_outcome_rejects_empty_body(self):
        """Recording with neither P&L nor outcome should return 422."""
        signal_id = str(uuid.uuid4())

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_record_outcome_signal_not_found(self):
        """Recording on a non-existent signal should return 404."""
        signal_id = str(uuid.uuid4())
        self._mock_db_pool([
            None,  # signal not found
        ])

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_outcome": "profit"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_record_outcome_invalid_outcome_value(self):
        """Invalid outcome value should return 422."""
        signal_id = str(uuid.uuid4())

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_outcome": "invalid_value"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_record_outcome_partial_update_preserves_existing(self):
        """Partial update should preserve existing values (COALESCE behavior)."""
        signal_id = str(uuid.uuid4())
        self._mock_db_pool([
            {"id": uuid.UUID(signal_id)},
            _make_signal_row(
                id=uuid.UUID(signal_id),
                user_pnl_usd=2000.00,
                user_outcome="profit",  # preserved from first call
                user_notes=None,
                reviewed_at=MagicMock(isoformat=MagicMock(return_value="2026-01-15T14:00:00+00:00")),
            ),
        ])

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_pnl_usd": 2000.00},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()["signal"]
        self.assertEqual(data["user_pnl_usd"], 2000.00)
        self.assertEqual(data["user_outcome"], "profit")  # preserved

    def test_record_outcome_pnl_range_validation(self):
        """P&L outside ±$1M should be rejected."""
        signal_id = str(uuid.uuid4())

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_pnl_usd": 2_000_000},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_record_outcome_notes_too_long(self):
        """Notes over 500 chars should be rejected."""
        signal_id = str(uuid.uuid4())

        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_outcome": "profit", "user_notes": "x" * 501},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_record_outcome_requires_auth(self):
        """Endpoint should require authentication."""
        signal_id = str(uuid.uuid4())
        resp = self.client.post(
            f"/api/signals/{signal_id}/outcome",
            json={"user_outcome": "profit"},
        )
        self.assertIn(resp.status_code, (401, 403))


# ─── Test: GET /api/signals/outcome-stats ─────────────────────────────


class TestOutcomeStats(unittest.TestCase):
    """Tests for GET /api/signals/outcome-stats.

    Uses the same DB pool mocking pattern as TestSignalStatsEndpoint.
    """

    def setUp(self):
        import main as main_mod
        self._main_mod = main_mod
        from fastapi.testclient import TestClient
        self.client = TestClient(main_mod.app)
        self.user_id = str(uuid.uuid4())
        self.wallet = "0x" + "b" * 40
        self.token = main_mod.create_jwt(self.wallet, self.user_id)

    def _mock_db_pool(self, overall_row, tier_rows, conf_rows):
        """Replace main.db_pool with a mock for the stats queries."""
        main_mod = self._main_mod
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=overall_row)
        mock_conn.fetch = AsyncMock(side_effect=[tier_rows, conf_rows])

        mock_pool = MagicMock()
        mock_pool.acquire = lambda: _AsyncCtxMgr(mock_conn)

        self._pool_patcher = patch("main.db_pool", mock_pool)
        self._pool_patcher.start()

    def tearDown(self):
        if hasattr(self, "_pool_patcher"):
            try:
                self._pool_patcher.stop()
            except RuntimeError:
                pass
        self._main_mod.db_pool = None

    def test_outcome_stats_with_data(self):
        """Stats endpoint should return correct aggregates."""
        overall = _make_outcome_stats_row()
        tier_rows = [
            _make_tier_row("high", reviewed=5, total_pnl=8000, avg_pnl=1600, profit_count=4),
            _make_tier_row("medium", reviewed=3, total_pnl=2000, avg_pnl=667, profit_count=2),
            _make_tier_row("low", reviewed=2, total_pnl=-1000, avg_pnl=-500, profit_count=0),
        ]
        conf_rows = [
            _make_conf_row("high", reviewed=4, total_pnl=7000, avg_pnl=1750),
            _make_conf_row("medium", reviewed=4, total_pnl=3000, avg_pnl=750),
            _make_conf_row("low", reviewed=2, total_pnl=-1000, avg_pnl=-500),
        ]
        self._mock_db_pool(overall, tier_rows, conf_rows)

        resp = self.client.get(
            "/api/signals/outcome-stats",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()

        self.assertEqual(data["total_reviewed"], 10)
        self.assertEqual(data["by_outcome"]["profit"], 6)
        self.assertEqual(data["by_outcome"]["loss"], 3)
        self.assertEqual(data["by_outcome"]["breakeven"], 1)
        self.assertEqual(data["by_outcome"]["skipped"], 0)
        self.assertEqual(data["total_pnl_usd"], 15000.00)
        self.assertEqual(data["avg_pnl_usd"], 1500.00)
        self.assertAlmostEqual(data["win_rate"], 0.6)
        self.assertEqual(data["avg_profit_usd"], 3000.00)
        self.assertEqual(data["avg_loss_usd"], -1000.00)

        # Check tier breakdown
        self.assertEqual(data["pnl_by_whale_tier"]["high"]["reviewed"], 5)
        self.assertAlmostEqual(data["pnl_by_whale_tier"]["high"]["win_rate"], 0.8)
        self.assertAlmostEqual(data["pnl_by_whale_tier"]["low"]["win_rate"], 0.0)

        # Check confidence breakdown
        self.assertEqual(data["pnl_by_confidence_tier"]["high"]["reviewed"], 4)
        self.assertAlmostEqual(data["pnl_by_confidence_tier"]["high"]["avg_pnl_usd"], 1750.0)

    def test_outcome_stats_empty(self):
        """Stats endpoint with no reviewed signals should return zeros."""
        overall = _make_outcome_stats_row(
            total_reviewed=0,
            profit_count=0,
            loss_count=0,
            breakeven_count=0,
            skipped_count=0,
            total_pnl_usd=None,
            avg_pnl_usd=None,
            avg_profit_usd=None,
            avg_loss_usd=None,
        )
        self._mock_db_pool(overall, [], [])

        resp = self.client.get(
            "/api/signals/outcome-stats",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200, f"Status {resp.status_code}: {resp.text}")
        data = resp.json()

        self.assertEqual(data["total_reviewed"], 0)
        self.assertEqual(data["win_rate"], 0.0)
        self.assertEqual(data["total_pnl_usd"], 0.0)
        self.assertEqual(data["avg_pnl_usd"], 0.0)
        self.assertEqual(data["pnl_by_whale_tier"], {})
        self.assertEqual(data["pnl_by_confidence_tier"], {})

    def test_outcome_stats_requires_auth(self):
        """Stats endpoint should require authentication."""
        resp = self.client.get("/api/signals/outcome-stats")
        self.assertIn(resp.status_code, (401, 403))
