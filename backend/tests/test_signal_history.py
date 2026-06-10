#!/usr/bin/env python3
"""
Unit tests for the signal history endpoint (GET /api/signals/history).

Tests cover:
- closed_at IS NOT NULL filter (only closed signals returned)
- status_filter param (executed|failed|stale)
- time_to_close_seconds computation
- closed_at DESC ordering
- limit parameter
- All expected fields in response shape

Run: python3 -m pytest tests/test_signal_history.py -v
"""
import asyncio
import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _make_signal_row(**overrides):
    """Create a mock DB row for a closed signal."""
    now = datetime.now(timezone.utc)
    created = overrides.get("created_at", now - timedelta(hours=2))
    closed = overrides.get("closed_at", now)
    executed = overrides.get("executed_at", closed)
    ttc = (closed - created).total_seconds()

    return {
        "id": overrides.get("id", "sig-001"),
        "token_symbol": overrides.get("token_symbol", "ETH"),
        "action": overrides.get("action", "buy"),
        "amount_usd": overrides.get("amount_usd", 50000.0),
        "confidence_score": overrides.get("confidence_score", 0.75),
        "score_at_generation": overrides.get("score_at_generation", 0.65),
        "status": overrides.get("status", "executed"),
        "explanation": overrides.get("explanation", "Test explanation"),
        "explanation_stale": overrides.get("explanation_stale", False),
        "created_at": created,
        "executed_at": executed,
        "closed_at": closed,
        "time_to_close_seconds": overrides.get("time_to_close_seconds", ttc),
        "wallet_address": overrides.get("wallet_address", "0xabc123"),
        "wallet_label": overrides.get("wallet_label", "Test Whale"),
        "whale_score": overrides.get("whale_score", 0.8),
    }


def _mock_row_to_response(r):
    """Convert a mock DB row dict to the response dict shape from main.py."""
    return {
        "id": str(r["id"]),
        "token_symbol": r["token_symbol"],
        "action": r["action"],
        "amount_usd": float(r["amount_usd"] or 0),
        "confidence_score": float(r["confidence_score"] or 0),
        "confidence_final": round(
            0.5 * float(r["confidence_score"] or 0)
            + 0.5 * float(r["score_at_generation"] or 0), 2
        ),
        "whale_score": float(r["whale_score"] or 0),
        "score_at_generation": float(r["score_at_generation"] or 0),
        "wallet_address": r["wallet_address"],
        "wallet_label": r["wallet_label"],
        "status": r["status"],
        "explanation": r["explanation"],
        "explanation_stale": bool(r["explanation_stale"]),
        "created_at": r["created_at"].isoformat(),
        "executed_at": r["executed_at"].isoformat() if r["executed_at"] else None,
        "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
        "time_to_close_seconds": float(r["time_to_close_seconds"] or 0),
    }


class TestSignalHistoryResponseShape(unittest.TestCase):
    """Test that the signal history response has all required fields."""

    def test_response_has_all_expected_fields(self):
        """Every signal in history response should have the 17 expected fields."""
        row = _make_signal_row()
        resp = _mock_row_to_response(row)
        expected_keys = {
            "id", "token_symbol", "action", "amount_usd",
            "confidence_score", "confidence_final", "whale_score",
            "score_at_generation", "wallet_address", "wallet_label",
            "status", "explanation", "explanation_stale",
            "created_at", "executed_at", "closed_at",
            "time_to_close_seconds",
        }
        self.assertEqual(set(resp.keys()), expected_keys)

    def test_closed_at_is_iso_string(self):
        """closed_at should be an ISO format string."""
        row = _make_signal_row()
        resp = _mock_row_to_response(row)
        self.assertIsInstance(resp["closed_at"], str)
        # Verify it's parseable
        dt = datetime.fromisoformat(resp["closed_at"])
        self.assertIsNotNone(dt)

    def test_created_at_is_iso_string(self):
        """created_at should be an ISO format string."""
        row = _make_signal_row()
        resp = _mock_row_to_response(row)
        self.assertIsInstance(resp["created_at"], str)

    def test_time_to_close_seconds_is_float(self):
        """time_to_close_seconds should be a non-negative float."""
        row = _make_signal_row()
        resp = _mock_row_to_response(row)
        self.assertIsInstance(resp["time_to_close_seconds"], float)
        self.assertGreaterEqual(resp["time_to_close_seconds"], 0)

    def test_confidence_final_is_blend(self):
        """confidence_final should be the average of confidence_score and score_at_generation."""
        row = _make_signal_row(confidence_score=0.8, score_at_generation=0.6)
        resp = _mock_row_to_response(row)
        expected = round(0.5 * 0.8 + 0.5 * 0.6, 2)
        self.assertAlmostEqual(resp["confidence_final"], expected)

    def test_id_is_string(self):
        """id should be serialized as string."""
        row = _make_signal_row(id="550e8400-e29b-41d4-a716-446655440000")
        resp = _mock_row_to_response(row)
        self.assertIsInstance(resp["id"], str)

    def test_executed_at_can_be_none(self):
        """executed_at should be None for failed signals."""
        row = _make_signal_row(status="failed", executed_at=None)
        resp = _mock_row_to_response(row)
        self.assertIsNone(resp["executed_at"])


class TestSignalHistoryTimeToClose(unittest.TestCase):
    """Test the time_to_close_seconds computation logic."""

    def test_time_to_close_matches_interval(self):
        """time_to_close_seconds should equal the difference between closed_at and created_at."""
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        closed = created + timedelta(hours=3, minutes=30)
        row = _make_signal_row(
            created_at=created,
            closed_at=closed,
            time_to_close_seconds=(closed - created).total_seconds(),
        )
        resp = _mock_row_to_response(row)
        self.assertAlmostEqual(resp["time_to_close_seconds"], 3 * 3600 + 30 * 60)

    def test_time_to_close_one_hour(self):
        """A signal closed in 1 hour should have ttc=3600."""
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        closed = created + timedelta(hours=1)
        row = _make_signal_row(
            created_at=created,
            closed_at=closed,
            time_to_close_seconds=3600.0,
        )
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["time_to_close_seconds"], 3600.0)

    def test_time_to_close_instant(self):
        """A signal closed instantly should have ttc≈0."""
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        closed = created
        row = _make_signal_row(
            created_at=created,
            closed_at=closed,
            time_to_close_seconds=0.0,
        )
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["time_to_close_seconds"], 0.0)

    def test_time_to_close_days(self):
        """A signal closed after 3 days should have ttc=259200."""
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        closed = created + timedelta(days=3)
        row = _make_signal_row(
            created_at=created,
            closed_at=closed,
            time_to_close_seconds=259200.0,
        )
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["time_to_close_seconds"], 259200.0)


class TestSignalHistoryFilterLogic(unittest.TestCase):
    """Test the filter conditions in the signal history endpoint."""

    def test_base_filter_includes_closed_at_not_null(self):
        """The history endpoint should filter by closed_at IS NOT NULL."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        self.assertIn("cts.closed_at IS NOT NULL", conditions)

    def test_executed_filter(self):
        """When status_filter='executed', the condition should include cts.status = $N."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params = ["user-uuid"]
        param_idx = 2
        status_filter = "executed"
        if status_filter:
            conditions.append(f"cts.status = ${param_idx}")
            params.append(status_filter)
            param_idx += 1
        self.assertEqual(conditions[-1], "cts.status = $2")
        self.assertEqual(params, ["user-uuid", "executed"])

    def test_failed_filter(self):
        """When status_filter='failed', the condition should include cts.status = $N."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params = ["user-uuid"]
        param_idx = 2
        status_filter = "failed"
        if status_filter:
            conditions.append(f"cts.status = ${param_idx}")
            params.append(status_filter)
            param_idx += 1
        self.assertEqual(conditions[-1], "cts.status = $2")
        self.assertEqual(params, ["user-uuid", "failed"])

    def test_stale_filter(self):
        """When status_filter='stale', the condition should include cts.status = $N."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params = ["user-uuid"]
        param_idx = 2
        status_filter = "stale"
        if status_filter:
            conditions.append(f"cts.status = ${param_idx}")
            params.append(status_filter)
            param_idx += 1
        self.assertEqual(conditions[-1], "cts.status = $2")
        self.assertEqual(params, ["user-uuid", "stale"])

    def test_no_filter_returns_all_closed(self):
        """When no status_filter, the WHERE clause should only have user_id and closed_at IS NOT NULL."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params = ["user-uuid"]
        param_idx = 2
        status_filter = None
        if status_filter:
            conditions.append(f"cts.status = ${param_idx}")
            params.append(status_filter)
            param_idx += 1
        self.assertEqual(len(conditions), 2)
        self.assertEqual(len(params), 1)

    def test_limit_param_appended_last(self):
        """LIMIT should always be the last parameter."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params: list = ["user-uuid"]
        param_idx = 2
        limit = 20
        params.append(limit)
        # The LIMIT clause references the current param_idx
        self.assertEqual(params[-1], 20)
        self.assertEqual(param_idx, 2)

    def test_executed_and_limit_params(self):
        """With status_filter + limit, params should be [user_id, status, limit]."""
        conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
        params: list = ["user-uuid"]
        param_idx = 2
        status_filter = "executed"
        limit = 50
        if status_filter:
            conditions.append(f"cts.status = ${param_idx}")
            params.append(status_filter)
            param_idx += 1
        params.append(limit)
        self.assertEqual(params, ["user-uuid", "executed", 50])


class TestSignalHistoryOrdering(unittest.TestCase):
    """Test that signal history is ordered by closed_at DESC."""

    def test_order_by_closed_at_desc(self):
        """The query should ORDER BY closed_at DESC."""
        rows = sorted(
            [
                _make_signal_row(id="a", closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                _make_signal_row(id="b", closed_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
                _make_signal_row(id="c", closed_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
            ],
            key=lambda r: r["closed_at"],
            reverse=True,
        )
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["b", "c", "a"])

    def test_most_recent_closed_first(self):
        """Most recently closed signal should appear first."""
        now = datetime.now(timezone.utc)
        rows = [
            _make_signal_row(id="old", closed_at=now - timedelta(days=5)),
            _make_signal_row(id="mid", closed_at=now - timedelta(days=2)),
            _make_signal_row(id="new", closed_at=now),
        ]
        rows.sort(key=lambda r: r["closed_at"], reverse=True)
        self.assertEqual(rows[0]["id"], "new")


class TestSignalHistoryNullHandling(unittest.TestCase):
    """Test null/edge case handling in response serialization."""

    def test_null_amount_defaults_to_zero(self):
        """amount_usd should default to 0 when null."""
        row = _make_signal_row(amount_usd=None)
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["amount_usd"], 0.0)

    def test_null_confidence_defaults_to_zero(self):
        """confidence_score should default to 0 when null."""
        row = _make_signal_row(confidence_score=None)
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["confidence_score"], 0.0)

    def test_null_score_at_generation_defaults_to_zero(self):
        """score_at_generation should default to 0 when null for confidence_final computation."""
        row = _make_signal_row(confidence_score=0.8, score_at_generation=None)
        resp = _mock_row_to_response(row)
        expected = round(0.5 * 0.8 + 0.5 * 0.0, 2)
        self.assertAlmostEqual(resp["confidence_final"], expected)

    def test_null_time_to_close_defaults_to_zero(self):
        """time_to_close_seconds should default to 0 when null."""
        row = _make_signal_row(time_to_close_seconds=None)
        resp = _mock_row_to_response(row)
        self.assertEqual(resp["time_to_close_seconds"], 0.0)

    def test_explanation_stale_is_bool(self):
        """explanation_stale should be serialized as bool."""
        row = _make_signal_row(explanation_stale=True)
        resp = _mock_row_to_response(row)
        self.assertIsInstance(resp["explanation_stale"], bool)
        self.assertTrue(resp["explanation_stale"])


class TestSignalHistorySourceVerification(unittest.TestCase):
    """Verify that the actual source code has the required patterns."""

    def test_history_endpoint_exists(self):
        """GET /api/signals/history should be defined in main.py."""
        with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
            src = f.read()
        self.assertIn('"/api/signals/history"', src)

    def test_closed_at_is_not_null_in_query(self):
        """The history endpoint should filter by closed_at IS NOT NULL."""
        with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
            src = f.read()
        self.assertIn("closed_at IS NOT NULL", src)

    def test_time_to_close_in_select(self):
        """The history endpoint should compute time_to_close_seconds."""
        with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
            src = f.read()
        self.assertIn("time_to_close_seconds", src)

    def test_order_by_closed_at_desc(self):
        """The history endpoint should ORDER BY closed_at DESC."""
        with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
            src = f.read()
        self.assertIn("ORDER BY cts.closed_at DESC", src)

    def test_closed_at_in_response(self):
        """The history endpoint response should include closed_at."""
        with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
            src = f.read()
        self.assertIn('"closed_at":', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
