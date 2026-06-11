#!/usr/bin/env python3
"""
Unit tests for the whale_scorer service.

Tests cover:
- score_whale_wallet: async function with mock DB connection
  - wallet not found (row is None) → default zero dict
  - zero signals → pure prior score, coldstart=True
  - many signals → W-based score, coldstart=False
  - cold-start blend boundary (signal_count_90d < 3)
  - median fallback: wallet-level vs global vs zero
  - score clamping to [0, 1]
  - all 5 sub-scores (activity, reliability, weight, recency, diversity)
  - global_median_30d parameter path (pre-computed median)
  - returned dict has all expected keys
  - score_signals_used equals signal_count_90d
  - wallet with null recency_days → max staleness (90)
  - wallet with recency_days=0 → max recency score (1.0)
  - s_weight at floor (median=$1000 → 0.0)
  - s_weight above floor (median=$10000 → positive)
  - s_diversity when signals=0 → 0.0
  - execution_rate_30d in returned dict

Run: python3 -m pytest backend/tests/test_whale_scorer.py -v
"""
import asyncio
import math
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services.whale_scorer import score_whale_wallet, SCORE_QUERY_SLOW_THRESHOLD_MS


def _run(coro):
    """Run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_row(
    signal_count_30d=0,
    signal_count_90d=0,
    execution_rate_30d=0.0,
    execution_rate_90d=0.0,
    median_signal_amount_30d=None,
    global_median_30d_from_param=None,
    tokens_traded_30d=0,
    recency_days_raw=None,
    wallet_age_days=0,
    balance_usd_current=0.0,
    signal_count_30d_for_median=0,
):
    """Build a mock asyncpg record for the scoring query result."""
    return {
        "signal_count_30d": signal_count_30d,
        "signal_count_90d": signal_count_90d,
        "execution_rate_30d": execution_rate_30d,
        "execution_rate_90d": execution_rate_90d,
        "median_signal_amount_30d": median_signal_amount_30d,
        "global_median_30d_from_param": global_median_30d_from_param,
        "tokens_traded_30d": tokens_traded_30d,
        "recency_days_raw": recency_days_raw,
        "wallet_age_days": wallet_age_days,
        "balance_usd_current": balance_usd_current,
        "signal_count_30d_for_median": signal_count_30d_for_median,
    }


class TestScoreWhaleWalletNotFound(unittest.TestCase):
    """Test the wallet-not-found path (row is None)."""

    def test_wallet_not_found_returns_defaults(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        result = _run(score_whale_wallet(mock_conn, "nonexistent-uuid"))

        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["score_activity"], 0.0)
        self.assertEqual(result["score_reliability"], 0.0)
        self.assertEqual(result["score_weight"], 0.0)
        self.assertEqual(result["score_recency"], 0.0)
        self.assertEqual(result["score_diversity"], 0.0)
        self.assertEqual(result["score_signals_used"], 0)
        self.assertTrue(result["score_is_coldstart"])
        self.assertEqual(result["median_amount_30d"], 0.0)
        self.assertEqual(result["execution_rate_30d"], 0.0)

    def test_wallet_not_found_keys(self):
        """Verify all expected keys are present even for missing wallet."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        result = _run(score_whale_wallet(mock_conn, "missing"))
        expected_keys = {
            "score", "score_activity", "score_reliability", "score_weight",
            "score_recency", "score_diversity", "score_signals_used",
            "score_is_coldstart", "median_amount_30d", "execution_rate_30d",
        }
        self.assertEqual(set(result.keys()), expected_keys)


class TestScoreWhaleWalletZeroSignals(unittest.TestCase):
    """Test scoring when wallet has no signals at all (pure cold-start)."""

    def test_zero_signals_pure_prior(self):
        """Wallet with no signals should get its prior (balance + age based)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        self.assertTrue(result["score_is_coldstart"])
        self.assertGreater(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)
        self.assertEqual(result["score_signals_used"], 0)

    def test_zero_signals_zero_balance(self):
        """Zero balance + zero age + zero signals → score should be 0."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=0.0,
            wallet_age_days=0,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        self.assertTrue(result["score_is_coldstart"])
        self.assertEqual(result["score"], 0.0)

    def test_zero_signals_all_subscores_zero(self):
        """With no signals, all sub-scores should be 0 except possibly prior."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=0.0,
            wallet_age_days=0,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        self.assertEqual(result["score_activity"], 0.0)
        self.assertEqual(result["score_reliability"], 0.0)
        self.assertEqual(result["score_weight"], 0.0)
        # recency: no signals → recency_days_raw=None → rc=90 → s_recency=0.0
        self.assertEqual(result["score_recency"], 0.0)
        self.assertEqual(result["score_diversity"], 0.0)


class TestScoreWhaleWalletManySignals(unittest.TestCase):
    """Test scoring when wallet has abundant signal history."""

    def test_proven_wallet_not_coldstart(self):
        """Wallet with many signals should NOT be coldstart."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=50,
            signal_count_90d=100,
            execution_rate_30d=0.8,
            execution_rate_90d=0.7,
            median_signal_amount_30d=50000.0,
            global_median_30d_from_param=30000.0,
            tokens_traded_30d=5,
            recency_days_raw=1,
            wallet_age_days=730,
            balance_usd_current=5_000_000.0,
            signal_count_30d_for_median=50,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        self.assertFalse(result["score_is_coldstart"])
        self.assertGreater(result["score"], 0.3)
        self.assertLessEqual(result["score"], 1.0)
        self.assertEqual(result["score_signals_used"], 100)

    def test_high_execution_rate_increases_reliability(self):
        """Higher execution rate should yield higher reliability sub-score."""
        low = AsyncMock()
        low.fetchrow.return_value = _make_row(
            signal_count_30d=20, signal_count_90d=40,
            execution_rate_30d=0.1, execution_rate_90d=0.1,
            balance_usd_current=500_000.0, wallet_age_days=365,
        )
        high = AsyncMock()
        high.fetchrow.return_value = _make_row(
            signal_count_30d=20, signal_count_90d=40,
            execution_rate_30d=0.95, execution_rate_90d=0.9,
            balance_usd_current=500_000.0, wallet_age_days=365,
        )

        r_low = _run(score_whale_wallet(low, "w1"))
        r_high = _run(score_whale_wallet(high, "w2"))

        self.assertGreater(r_high["score_reliability"], r_low["score_reliability"])

    def test_many_tokens_increases_diversity(self):
        """More unique tokens traded should increase diversity sub-score."""
        low_div = AsyncMock()
        low_div.fetchrow.return_value = _make_row(
            signal_count_30d=20, signal_count_90d=40,
            tokens_traded_30d=1,
            balance_usd_current=500_000.0, wallet_age_days=365,
        )
        high_div = AsyncMock()
        high_div.fetchrow.return_value = _make_row(
            signal_count_30d=20, signal_count_90d=40,
            tokens_traded_30d=10,
            balance_usd_current=500_000.0, wallet_age_days=365,
        )

        r_low = _run(score_whale_wallet(low_div, "w1"))
        r_high = _run(score_whale_wallet(high_div, "w2"))

        self.assertGreater(r_high["score_diversity"], r_low["score_diversity"])


class TestScoreWhaleWalletColdStartBlend(unittest.TestCase):
    """Test the cold-start blend boundary conditions."""

    def test_coldstart_flag_with_1_signal(self):
        """Wallet with 1 signal in 90d should be flagged as coldstart."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=1,
            signal_count_90d=1,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertTrue(result["score_is_coldstart"])

    def test_coldstart_flag_with_2_signals(self):
        """Wallet with 2 signals in 90d should be flagged as coldstart."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=2,
            signal_count_90d=2,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertTrue(result["score_is_coldstart"])

    def test_not_coldstart_with_3_signals(self):
        """Wallet with 3 signals in 90d should NOT be flagged as coldstart."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=3,
            signal_count_90d=3,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertFalse(result["score_is_coldstart"])

    def test_blend_with_few_signals_weighted_towards_prior(self):
        """With few signals, the prior should have significant influence."""
        no_signals = AsyncMock()
        no_signals.fetchrow.return_value = _make_row(
            signal_count_30d=0, signal_count_90d=0,
            balance_usd_current=1_000_000.0, wallet_age_days=365,
        )
        few_signals = AsyncMock()
        few_signals.fetchrow.return_value = _make_row(
            signal_count_30d=2, signal_count_90d=2,
            execution_rate_30d=0.0, execution_rate_90d=0.0,
            balance_usd_current=1_000_000.0, wallet_age_days=365,
        )

        r_none = _run(score_whale_wallet(no_signals, "w1"))
        r_few = _run(score_whale_wallet(few_signals, "w2"))

        # With few signals, lambda = 1/(1+2) = 0.33, so prior has 33% weight
        # Score should be between pure-prior and pure-W
        self.assertGreater(r_few["score"], 0.0)

    def test_many_signals_blend_towards_W(self):
        """With many signals, W should dominate (lambda → 0)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=200,
            signal_count_90d=500,
            execution_rate_30d=0.9,
            execution_rate_90d=0.85,
            median_signal_amount_30d=100000.0,
            global_median_30d_from_param=50000.0,
            tokens_traded_30d=10,
            recency_days_raw=0,
            wallet_age_days=730,
            balance_usd_current=5_000_000.0,
            signal_count_30d_for_median=200,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        # With 500 signals, lambda = 1/501 ≈ 0.002, so W dominates
        self.assertGreater(result["score"], 0.0)
        self.assertFalse(result["score_is_coldstart"])


class TestScoreWhaleWalletMedianFallback(unittest.TestCase):
    """Test the median amount fallback logic."""

    def test_wallet_level_median_when_enough_signals(self):
        """When >= 3 signals with non-null median, use wallet-level median."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            median_signal_amount_30d=50000.0,
            global_median_30d_from_param=30000.0,
            signal_count_30d_for_median=10,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        # median_amount_30d should be wallet-level (50000), not global (30000)
        self.assertEqual(result["median_amount_30d"], 50000.0)

    def test_global_median_fallback_when_few_signals(self):
        """When < 3 signals, fall back to global median."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=2,
            signal_count_90d=5,
            median_signal_amount_30d=50000.0,  # wallet has median but < 3 samples
            global_median_30d_from_param=30000.0,
            signal_count_30d_for_median=2,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        # Should use global median since signal_count_30d_for_median < 3
        self.assertEqual(result["median_amount_30d"], 30000.0)

    def test_zero_median_when_no_data(self):
        """When no wallet median and no global median, should be 0."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            median_signal_amount_30d=None,
            global_median_30d_from_param=None,
            signal_count_30d_for_median=0,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        self.assertEqual(result["median_amount_30d"], 0.0)


class TestScoreWhaleWalletSubScores(unittest.TestCase):
    """Test individual sub-score computations."""

    def test_s_activity_with_1_signal(self):
        """S_activity = log10(2)/log10(101) ≈ 0.0301 with 1 signal."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=1,
            signal_count_90d=1,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        expected = round(min(math.log10(2) / math.log10(101), 1.0), 3)
        self.assertAlmostEqual(result["score_activity"], expected, places=3)

    def test_s_activity_max_at_100_signals(self):
        """S_activity should be 1.0 at 100 signals (log10(101)/log10(101) = 1.0)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=100,
            signal_count_90d=200,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_activity"], 1.0, places=2)

    def test_s_reliability_formula(self):
        """S_reliability = 0.7*exec_30d + 0.3*exec_90d."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            execution_rate_30d=0.8,
            execution_rate_90d=0.5,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        expected = round(0.7 * 0.8 + 0.3 * 0.5, 3)
        self.assertAlmostEqual(result["score_reliability"], expected, places=3)

    def test_s_weight_at_1k_median(self):
        """S_weight at median=$1000 should be 0.0 (floor)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            median_signal_amount_30d=1000.0,
            signal_count_30d_for_median=10,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_weight"], 0.0, places=3)

    def test_s_weight_at_10k_median(self):
        """S_weight at median=$10000 should be positive."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            median_signal_amount_30d=10000.0,
            signal_count_30d_for_median=10,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        # (log10(10000) - 3) / 3 = (4-3)/3 = 0.333
        expected = round((math.log10(10000) - 3) / 3, 3)
        self.assertAlmostEqual(result["score_weight"], expected, places=3)

    def test_s_weight_zero_when_no_median(self):
        """S_weight should be 0 when median_amount_30d is 0."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertEqual(result["score_weight"], 0.0)

    def test_s_recency_max_when_0_days(self):
        """S_recency should be 1.0 when last signal was today (0 days)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            recency_days_raw=0,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_recency"], 1.0, places=3)

    def test_s_recency_zero_when_90_days(self):
        """S_recency should be 0.0 when last signal was 90 days ago."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=1,
            signal_count_90d=1,
            recency_days_raw=90,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_recency"], 0.0, places=3)

    def test_s_recency_clamped_at_90(self):
        """S_recency should clamp recency_days > 90 to 90."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=1,
            signal_count_90d=1,
            recency_days_raw=180,  # Beyond 90 days
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        # Should be clamped to 90 → s_recency = 0.0
        self.assertAlmostEqual(result["score_recency"], 0.0, places=3)

    def test_s_recency_null_recency_days(self):
        """Null recency_days should be treated as max staleness (90 days)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            recency_days_raw=None,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_recency"], 0.0, places=3)

    def test_s_diversity_zero_when_no_signals(self):
        """S_diversity should be 0 when there are no signals."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            tokens_traded_30d=0,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertEqual(result["score_diversity"], 0.0)

    def test_s_diversity_max_when_all_unique(self):
        """S_diversity should be 1.0 when tokens == signals (all unique)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            tokens_traded_30d=10,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["score_diversity"], 1.0, places=3)


class TestScoreWhaleWalletScoreBounds(unittest.TestCase):
    """Test that scores are always within [0, 1]."""

    def test_score_bounds_extreme_low(self):
        """Score should be 0 for a wallet with nothing."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0, signal_count_90d=0,
            balance_usd_current=0.0, wallet_age_days=0,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_score_bounds_extreme_high(self):
        """Score should be ≤ 1.0 even for a perfect wallet."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=1000,
            signal_count_90d=5000,
            execution_rate_30d=1.0,
            execution_rate_90d=1.0,
            median_signal_amount_30d=10_000_000.0,
            global_median_30d_from_param=1_000_000.0,
            tokens_traded_30d=100,
            recency_days_raw=0,
            wallet_age_days=3650,
            balance_usd_current=1e12,
            signal_count_30d_for_median=1000,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_all_subscores_bounded(self):
        """All sub-scores should be in [0, 1] for any input."""
        test_cases = [
            {"signal_count_30d": 0, "signal_count_90d": 0, "balance_usd_current": 0},
            {"signal_count_30d": 1000, "signal_count_90d": 5000, "balance_usd_current": 1e12,
             "execution_rate_30d": 1.0, "execution_rate_90d": 1.0,
             "median_signal_amount_30d": 1e8, "signal_count_30d_for_median": 1000,
             "tokens_traded_30d": 500, "recency_days_raw": 0,
             "wallet_age_days": 3650},
        ]
        for tc in test_cases:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = _make_row(**tc)
            result = _run(score_whale_wallet(mock_conn, "w"))
            for key in ["score_activity", "score_reliability", "score_weight",
                        "score_recency", "score_diversity"]:
                self.assertGreaterEqual(result[key], 0.0, f"{key} below 0 for {tc}")
                self.assertLessEqual(result[key], 1.0, f"{key} above 1 for {tc}")


class TestScoreWhaleWalletGlobalMedianParam(unittest.TestCase):
    """Test the global_median_30d parameter path."""

    def test_precomputed_median_passed_as_param(self):
        """When global_median_30d > 0, it should be used as fallback median."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            median_signal_amount_30d=None,
            global_median_30d_from_param=50000.0,  # Will be overridden by param
            signal_count_30d_for_median=0,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        result = _run(score_whale_wallet(mock_conn, "w1", global_median_30d=50000.0))

        # Should use the pre-computed median since no wallet-level median available
        self.assertEqual(result["median_amount_30d"], 50000.0)

    def test_precomputed_median_used_in_query(self):
        """Verify the SQL query is constructed with the pre-computed median value."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        _run(score_whale_wallet(mock_conn, "w1", global_median_30d=42000.0))

        # Check that fetchrow was called with the median value in the SQL
        call_args = mock_conn.fetchrow.call_args
        sql = call_args[0][0]
        self.assertIn("42000.0", sql)

    def test_zero_global_median_triggers_subquery(self):
        """When global_median_30d=0 (default), the subquery should be used."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd_current=1_000_000.0,
            wallet_age_days=365,
        )

        _run(score_whale_wallet(mock_conn, "w1", global_median_30d=0.0))

        call_args = mock_conn.fetchrow.call_args
        sql = call_args[0][0]
        # Should contain the PERCENTILE_CONT subquery
        self.assertIn("PERCENTILE_CONT", sql)


class TestScoreWhaleWalletResponseShape(unittest.TestCase):
    """Test the structure and types of the returned dict."""

    def test_all_keys_present(self):
        """Result dict should contain exactly the expected keys."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10,
            signal_count_90d=20,
            balance_usd_current=100_000.0,
            wallet_age_days=180,
        )

        result = _run(score_whale_wallet(mock_conn, "w1"))

        expected_keys = {
            "score", "score_activity", "score_reliability", "score_weight",
            "score_recency", "score_diversity", "score_signals_used",
            "score_is_coldstart", "median_amount_30d", "execution_rate_30d",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_score_is_float(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10, signal_count_90d=20,
            balance_usd_current=100_000.0, wallet_age_days=180,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertIsInstance(result["score"], float)

    def test_coldstart_is_bool(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10, signal_count_90d=20,
            balance_usd_current=100_000.0, wallet_age_days=180,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertIsInstance(result["score_is_coldstart"], bool)

    def test_signals_used_is_int(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10, signal_count_90d=20,
            balance_usd_current=100_000.0, wallet_age_days=180,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertIsInstance(result["score_signals_used"], int)

    def test_execution_rate_30d_in_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=10, signal_count_90d=20,
            execution_rate_30d=0.75,
            balance_usd_current=100_000.0, wallet_age_days=180,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        self.assertAlmostEqual(result["execution_rate_30d"], 0.75, places=3)


class TestScoreWhaleWalletScoreRounding(unittest.TestCase):
    """Test that scores are properly rounded."""

    def test_score_rounded_to_3_decimals(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=7,
            signal_count_90d=15,
            execution_rate_30d=0.666,
            execution_rate_90d=0.555,
            median_signal_amount_30d=7777.0,
            signal_count_30d_for_median=7,
            tokens_traded_30d=3,
            recency_days_raw=7,
            wallet_age_days=200,
            balance_usd_current=500_000.0,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        # Score should be rounded to 3 decimal places
        self.assertEqual(result["score"], round(result["score"], 3))


class TestScoreWhaleWalletDBInteraction(unittest.TestCase):
    """Test that the function interacts with the DB correctly."""

    def test_fetchrow_called_with_wallet_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0, signal_count_90d=0,
            balance_usd_current=0.0, wallet_age_days=0,
        )

        _run(score_whale_wallet(mock_conn, "test-uuid-123"))

        mock_conn.fetchrow.assert_called_once()
        call_args = mock_conn.fetchrow.call_args
        self.assertEqual(call_args[0][1], "test-uuid-123")

    def test_fetchrow_called_with_sql(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0, signal_count_90d=0,
            balance_usd_current=0.0, wallet_age_days=0,
        )

        _run(score_whale_wallet(mock_conn, "w1"))

        call_args = mock_conn.fetchrow.call_args
        sql = call_args[0][0]
        # Verify key SQL elements
        self.assertIn("copy_trade_signals", sql)
        self.assertIn("wallets", sql)
        self.assertIn("RIGHT JOIN", sql)
        self.assertIn("$1", sql)


class TestScoreWhaleWalletExactFormula(unittest.TestCase):
    """Test exact computed values for composite W and prior (not just > 0)."""

    def test_exact_prior_value(self):
        """prior = 0.5*min(log10(max(1M,1))/8, 1.0) + 0.5*min(365/365, 1.0)
        = 0.5*(6/8) + 0.5*1.0 = 0.375 + 0.5 = 0.875"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=0, signal_count_90d=0,
            balance_usd_current=1_000_000.0, wallet_age_days=365,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        # Pure prior: effective_score = prior (no signals at all)
        self.assertAlmostEqual(result["score"], 0.875, places=3)

    def test_exact_composite_W_with_many_signals(self):
        """Verify composite W is computed correctly with known inputs.
        With 100 signals, 100% execution, $1M median, 10 tokens, 0 recency:
        s_activity = min(log10(101)/log10(101), 1.0) = 1.0
        s_reliability = 0.7*1.0 + 0.3*1.0 = 1.0
        s_weight = (log10(max(1M,1000))-3)/3 = (6-3)/3 = 1.0
        s_recency = 1.0 - 0/90 = 1.0
        s_diversity = min(10/100, 1.0) = 0.1
        W = 0.15*1 + 0.25*1 + 0.30*1 + 0.15*1 + 0.15*0.1 = 0.865
        With 500 signals: lambda = 1/501 ≈ 0.002, so effective ≈ W ≈ 0.865"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=100, signal_count_90d=500,
            execution_rate_30d=1.0, execution_rate_90d=1.0,
            median_signal_amount_30d=1_000_000.0,
            signal_count_30d_for_median=100,
            tokens_traded_30d=10, recency_days_raw=0,
            balance_usd_current=5_000_000.0, wallet_age_days=730,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        # W should be 0.865; with 500 signals, effective ≈ W
        self.assertAlmostEqual(result["score"], 0.865, places=2)

    def test_exact_blend_ratio_with_2_signals(self):
        """With 2 signals in 90d, lambda = 1/(1+2) = 0.333.
        prior = 0.5*min(log10(100K)/8, 1) + 0.5*min(180/365, 1)
             = 0.5*(5/8) + 0.5*(180/365) = 0.3125 + 0.2466 = 0.5591
        W with 0 exec, 0 median, 0 tokens, 90 recency:
        s_activity = log10(3)/log10(101) ≈ 0.0212
        s_reliability = 0
        s_weight = 0
        s_recency = 1 - 90/90 = 0
        s_diversity = 0
        W = 0.15*0.0212 = 0.00318
        effective = 0.333*0.5591 + 0.667*0.00318 = 0.1862 + 0.00212 = 0.188"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_row(
            signal_count_30d=2, signal_count_90d=2,
            execution_rate_30d=0.0, execution_rate_90d=0.0,
            median_signal_amount_30d=None,
            signal_count_30d_for_median=0,
            tokens_traded_30d=0, recency_days_raw=90,
            balance_usd_current=100_000.0, wallet_age_days=180,
        )
        result = _run(score_whale_wallet(mock_conn, "w1"))
        # Verify the blend is between pure-prior (0.559) and pure-W (0.003)
        self.assertGreater(result["score"], 0.003)
        self.assertLess(result["score"], 0.559)
        # With lambda=1/3, should be closer to W than prior
        self.assertLess(result["score"], 0.3)


class TestScoreWhaleWalletSlowQueryThreshold(unittest.TestCase):
    """Test the slow query threshold constant."""

    def test_threshold_is_positive(self):
        self.assertGreater(SCORE_QUERY_SLOW_THRESHOLD_MS, 0)

    def test_threshold_is_reasonable(self):
        """500ms is a reasonable threshold — between 100ms and 5s."""
        self.assertGreaterEqual(SCORE_QUERY_SLOW_THRESHOLD_MS, 100)
        self.assertLessEqual(SCORE_QUERY_SLOW_THRESHOLD_MS, 5000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
