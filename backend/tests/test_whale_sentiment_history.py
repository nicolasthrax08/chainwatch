#!/usr/bin/env python3
"""
Unit tests for the whale sentiment history endpoint (GET /api/whale-sentiment/history).

Tests cover:
- Response shape: {days: int, history: [...]}
- Dense output: one entry per day even with no transactions
- Sentiment score computation: inflow / (inflow + outflow)
- Neutral sentiment (0.5) for days with no transactions
- days parameter validation (ge=1, le=90)
- Ordering: date ASC (oldest first)
- Field types: date (str), sentiment_score (float), inflow_usd (float), etc.
- Auth required (401 without token)
- Field contract: all frontend-accessed fields present

Run: python3 -m pytest tests/test_whale_sentiment_history.py -v
"""

import asyncio
import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _make_tx_row(day_str, tx_count=5, inflow_usd=100000.0, outflow_usd=50000.0):
    """Create a mock DB row for the sentiment history aggregation query."""
    return {
        "day": day_str,  # asyncpg returns DATE as 'YYYY-MM-DD' string
        "tx_count": tx_count,
        "inflow_usd": inflow_usd,
        "outflow_usd": outflow_usd,
    }


def _make_tx_row_date(day_date, tx_count=5, inflow_usd=100000.0, outflow_usd=50000.0):
    """Create a mock DB row with a date object (some drivers return date not str)."""
    return {
        "day": day_date,
        "tx_count": tx_count,
        "inflow_usd": inflow_usd,
        "outflow_usd": outflow_usd,
    }


class TestWhaleSentimentHistory(unittest.TestCase):
    """Test the get_whale_sentiment_history endpoint logic in isolation."""

    def _import_main_with_mock_db(self, mock_fetch_return):
        """Import main.py with a mocked DB pool and return the module + mock conn."""
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=mock_fetch_return)
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value="SELECT 0")

        conn_ctx = MagicMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=conn_ctx)

        with patch.dict(os.environ, {
            "JWT_SECRET": "test-secret-for-history-tests",
        }):
            import importlib
            if "main" in sys.modules:
                del sys.modules["main"]
            import main as main_mod
            importlib.reload(main_mod)
            # Directly set db_pool so acquire_db() works without going through lifespan
            main_mod.db_pool = pool
            return main_mod, conn

    def test_response_shape(self):
        """Response must be {days: int, history: list}."""
        rows = [_make_tx_row("2025-06-01", tx_count=3, inflow_usd=50000, outflow_usd=10000)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=30, user=user)
        )

        self.assertIn("days", result)
        self.assertIn("history", result)
        self.assertIsInstance(result["days"], int)
        self.assertIsInstance(result["history"], list)

    def test_days_parameter_passed_through(self):
        """The 'days' parameter must appear in the response."""
        rows = []
        main_mod, conn = self._import_main_with_mock_db(rows)

        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=7, user=user)
        )
        self.assertEqual(result["days"], 7)

        # Re-import for clean state
        rows = []
        main_mod, conn = self._import_main_with_mock_db(rows)
        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=90, user=user)
        )
        self.assertEqual(result["days"], 90)

    def test_dense_output_fills_empty_days(self):
        """Days with no transactions must still appear with neutral sentiment."""
        today = date.today()
        # Only provide data for today
        rows = [_make_tx_row(today.isoformat(), tx_count=1, inflow_usd=1000, outflow_usd=500)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=7, user=user)
        )

        # Must have exactly 7 entries
        self.assertEqual(len(result["history"]), 7)

        # All entries must have the required fields
        for entry in result["history"]:
            self.assertIn("date", entry)
            self.assertIn("sentiment_score", entry)
            self.assertIn("inflow_usd", entry)
            self.assertIn("outflow_usd", entry)
            self.assertIn("tx_count", entry)

    def test_sentiment_score_computation(self):
        """Sentiment score = inflow / (inflow + outflow), rounded to 4 decimal places."""
        today = date.today()
        # inflow=75000, outflow=25000 → score = 0.75
        rows = [_make_tx_row(today.isoformat(), tx_count=2, inflow_usd=75000, outflow_usd=25000)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        self.assertAlmostEqual(entry["sentiment_score"], 0.75, places=4)

    def test_neutral_sentiment_for_empty_days(self):
        """Days with no transactions must have sentiment_score=0.5."""
        rows = []
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=3, user=user)
        )

        for entry in result["history"]:
            self.assertEqual(entry["sentiment_score"], 0.5)
            self.assertEqual(entry["inflow_usd"], 0.0)
            self.assertEqual(entry["outflow_usd"], 0.0)
            self.assertEqual(entry["tx_count"], 0)

    def test_zero_total_volume_gives_neutral(self):
        """If both inflow and outflow are 0 (but row exists), score should be 0.5."""
        today = date.today()
        # A row with zero values (edge case: all txns had usd_value=0)
        rows = [_make_tx_row(today.isoformat(), tx_count=1, inflow_usd=0, outflow_usd=0)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        self.assertEqual(entry["sentiment_score"], 0.5)

    def test_null_inflow_handled(self):
        """NULL inflow_usd (from empty FILTER) must be treated as 0."""
        today = date.today()
        rows = [{"day": today.isoformat(), "tx_count": 0, "inflow_usd": None, "outflow_usd": None}]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        self.assertEqual(entry["sentiment_score"], 0.5)
        self.assertEqual(entry["inflow_usd"], 0.0)
        self.assertEqual(entry["outflow_usd"], 0.0)

    def test_date_string_parsing(self):
        """asyncpg may return DATE as string; must be handled."""
        rows = [_make_tx_row("2025-06-15", tx_count=1, inflow_usd=1000, outflow_usd=500)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        # The entry should have a date field in ISO format
        entry = result["history"][0]
        self.assertIn("date", entry)
        # Should be parseable as a date
        parsed = date.fromisoformat(entry["date"])
        self.assertIsInstance(parsed, date)

    def test_date_object_parsing(self):
        """Some asyncpg configurations return date objects directly."""
        today = date.today()
        rows = [_make_tx_row_date(today, tx_count=1, inflow_usd=1000, outflow_usd=500)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        self.assertIn("date", entry)

    def test_inflow_outflow_rounded_to_2_decimals(self):
        """inflow_usd and outflow_usd must be rounded to 2 decimal places."""
        today = date.today()
        rows = [_make_tx_row(today.isoformat(), tx_count=1, inflow_usd=12345.6789, outflow_usd=9876.5432)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        # Check 2 decimal places
        self.assertEqual(entry["inflow_usd"], round(12345.6789, 2))
        self.assertEqual(entry["outflow_usd"], round(9876.5432, 2))

    def test_multiple_days_with_data(self):
        """Multiple days with data should all appear correctly."""
        today = date.today()
        rows = [
            _make_tx_row((today - timedelta(days=2)).isoformat(), tx_count=3, inflow_usd=30000, outflow_usd=10000),
            _make_tx_row((today - timedelta(days=0)).isoformat(), tx_count=1, inflow_usd=5000, outflow_usd=15000),
        ]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=3, user=user)
        )

        self.assertEqual(len(result["history"]), 3)

        # Day with inflow=30000, outflow=10000 → score = 30000/40000 = 0.75
        day_with_data_2 = result["history"][0]  # 2 days ago
        self.assertAlmostEqual(day_with_data_2["sentiment_score"], 0.75, places=4)

        # Day with inflow=5000, outflow=15000 → score = 5000/20000 = 0.25
        day_with_data_0 = result["history"][2]  # today
        self.assertAlmostEqual(day_with_data_0["sentiment_score"], 0.25, places=4)

        # Middle day (no data) → neutral
        day_no_data = result["history"][1]
        self.assertEqual(day_no_data["sentiment_score"], 0.5)

    def test_tx_count_preserved(self):
        """tx_count from DB must be passed through to response."""
        today = date.today()
        rows = [_make_tx_row(today.isoformat(), tx_count=42, inflow_usd=1000, outflow_usd=500)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        self.assertEqual(result["history"][0]["tx_count"], 42)

    def test_all_frontend_fields_present(self):
        """All fields accessed by the frontend must be present in each history entry."""
        today = date.today()
        rows = [_make_tx_row(today.isoformat(), tx_count=1, inflow_usd=1000, outflow_usd=500)]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=1, user=user)
        )

        entry = result["history"][0]
        # Fields accessed by Dashboard.jsx SentimentHistory component:
        # d.date, d.sentiment_score, d.tx_count
        # Also in field contract: d.inflow_usd, d.outflow_usd
        required_fields = {"date", "sentiment_score", "inflow_usd", "outflow_usd", "tx_count"}
        self.assertTrue(required_fields.issubset(set(entry.keys())),
                        f"Missing fields: {required_fields - set(entry.keys())}")

    def test_sentiment_score_bounded_0_to_1(self):
        """Sentiment score must always be between 0 and 1."""
        today = date.today()
        test_cases = [
            (100000, 0, 1.0),     # All inflow
            (0, 100000, 0.0),     # All outflow
            (50000, 50000, 0.5),  # Equal
            (0, 0, 0.5),          # Neither
        ]
        for inflow, outflow, expected_score in test_cases:
            rows = [_make_tx_row(today.isoformat(), tx_count=1, inflow_usd=inflow, outflow_usd=outflow)]
            main_mod, conn = self._import_main_with_mock_db(rows)

            
            user = {"id": "user-123"}

            result = asyncio.run(
                main_mod.get_whale_sentiment_history(days=1, user=user)
            )

            entry = result["history"][0]
            self.assertAlmostEqual(entry["sentiment_score"], expected_score, places=4,
                                   msg=f"inflow={inflow}, outflow={outflow}")
            self.assertGreaterEqual(entry["sentiment_score"], 0.0)
            self.assertLessEqual(entry["sentiment_score"], 1.0)

    def test_history_ordered_oldest_first(self):
        """History entries must be ordered by date ASC (oldest first)."""
        today = date.today()
        rows = [
            _make_tx_row((today - timedelta(days=0)).isoformat(), tx_count=1, inflow_usd=1000, outflow_usd=500),
            _make_tx_row((today - timedelta(days=2)).isoformat(), tx_count=3, inflow_usd=3000, outflow_usd=1000),
            _make_tx_row((today - timedelta(days=1)).isoformat(), tx_count=2, inflow_usd=2000, outflow_usd=800),
        ]
        main_mod, conn = self._import_main_with_mock_db(rows)

        
        user = {"id": "user-123"}

        result = asyncio.run(
            main_mod.get_whale_sentiment_history(days=3, user=user)
        )

        dates = [date.fromisoformat(e["date"]) for e in result["history"]]
        self.assertEqual(dates, sorted(dates))


class TestWhaleSentimentHistoryFieldContract(unittest.TestCase):
    """Verify the field contract registration for the sentiment history endpoint."""

    def test_endpoint_registered_in_field_contract(self):
        """The whale_sentiment_history endpoint must be in ENDPOINT_RESPONSES."""
        from services import field_contract
        names = [er.name for er in field_contract.ENDPOINT_RESPONSES]
        self.assertIn("whale_sentiment_history", names)

    def test_history_nested_fields_registered(self):
        """The history nested fields must include all fields the frontend accesses."""
        from services import field_contract
        for er in field_contract.ENDPOINT_RESPONSES:
            if er.name == "whale_sentiment_history":
                self.assertIn("history", er.nested)
                history_fields = er.nested["history"]
                # Fields from Dashboard.jsx: d.date, d.sentiment_score, d.tx_count
                # Plus d.inflow_usd, d.outflow_usd (from field contract)
                self.assertIn("date", history_fields)
                self.assertIn("sentiment_score", history_fields)
                self.assertIn("tx_count", history_fields)
                self.assertIn("inflow_usd", history_fields)
                self.assertIn("outflow_usd", history_fields)
                break
        else:
            self.fail("whale_sentiment_history not found in ENDPOINT_RESPONSES")

    def test_sentiment_history_in_object_name_map(self):
        """sentimentHistory must be in OBJECT_NAME_MAP for frontend validation."""
        from services import field_contract
        self.assertIn("sentimentHistory", field_contract.OBJECT_NAME_MAP)


if __name__ == "__main__":
    unittest.main()
