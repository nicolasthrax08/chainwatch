#!/usr/bin/env python3
"""
Unit tests for ChainWatch signal lifecycle and whale scoring.

Tests:
- Signal stale expiry SQL logic (simulated)
- WhaleScorer.score_whale_wallet score computation
- SignalGenerator.evaluate_for_signal threshold logic
- SignalGenerator.generate_explanation template selection

Run: python3 -m pytest tests/ -v
"""
import asyncio
import math
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional

# Add backend to path so we can import services
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestWhaleScorerMath(unittest.TestCase):
    """Test the mathematical computations in whale_scorer without a DB connection."""

    def _compute_score(
        self,
        signal_count_30d: int = 0,
        signal_count_90d: int = 0,
        execution_rate_30d: float = 0.0,
        execution_rate_90d: float = 0.0,
        median_amount_30d: float = 0.0,
        tokens_traded_30d: int = 0,
        recency_days: Optional[int] = None,
        wallet_age_days: int = 0,
        balance_usd: float = 0.0,
    ) -> dict:
        """Replicate the scoring logic from whale_scorer.py for unit testing."""
        # S_activity
        s_activity = min(math.log10(signal_count_30d + 1) / math.log10(101), 1.0)

        # S_reliability
        s_reliability = 0.7 * execution_rate_30d + 0.3 * execution_rate_90d

        # S_weight
        if median_amount_30d > 0:
            s_weight = (math.log10(max(median_amount_30d, 1000)) - 3) / 3
            s_weight = max(0.0, min(s_weight, 1.0))
        else:
            s_weight = 0.0

        # S_recency
        if recency_days is not None:
            rc = min(recency_days, 90)
        else:
            rc = 90
        s_recency = 1.0 - rc / 90.0

        # S_diversity
        if signal_count_30d > 0:
            td = tokens_traded_30d / signal_count_30d
        else:
            td = 0.0
        s_diversity = max(0.0, min(td, 1.0))

        # Composite W
        W = (
            0.15 * s_activity
            + 0.25 * s_reliability
            + 0.30 * s_weight
            + 0.15 * s_recency
            + 0.15 * s_diversity
        )

        # Cold-start blend
        if signal_count_90d == 0:
            prior = (
                0.5 * min(math.log10(max(balance_usd, 1)) / 8, 1.0)
                + 0.5 * min(wallet_age_days / 365, 1.0)
            )
            effective_score = prior
            is_coldstart = True
        else:
            prior = (
                0.5 * min(math.log10(max(balance_usd, 1)) / 8, 1.0)
                + 0.5 * min(wallet_age_days / 365, 1.0)
            )
            lam = 1.0 / (1.0 + signal_count_90d)
            effective_score = lam * prior + (1 - lam) * W
            is_coldstart = signal_count_90d < 3

        effective_score = round(max(0.0, min(effective_score, 1.0)), 3)

        return {
            "score": effective_score,
            "W": round(W, 6),
            "s_activity": round(s_activity, 3),
            "s_reliability": round(s_reliability, 3),
            "s_weight": round(s_weight, 3),
            "s_recency": round(s_recency, 3),
            "s_diversity": round(s_diversity, 3),
            "is_coldstart": is_coldstart,
        }

    def test_zero_signals_gets_prior_only(self):
        """Wallet with no signals should get its prior (balance + age based score)."""
        result = self._compute_score(
            signal_count_30d=0,
            signal_count_90d=0,
            balance_usd=1_000_000,
            wallet_age_days=365,
        )
        self.assertTrue(result["is_coldstart"])
        self.assertGreater(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_high_activity_proven_wallet(self):
        """Proven wallet with many signals should have score based primarily on W."""
        result = self._compute_score(
            signal_count_30d=50,
            signal_count_90d=100,
            execution_rate_30d=0.8,
            execution_rate_90d=0.7,
            median_amount_30d=50000.0,
            tokens_traded_30d=5,
            recency_days=1,
            wallet_age_days=730,
            balance_usd=5_000_000,
        )
        self.assertFalse(result["is_coldstart"])
        self.assertGreater(result["score"], 0.3)
        self.assertLessEqual(result["score"], 1.0)

    def test_coldstart_flag_with_few_signals(self):
        """Wallet with < 3 signals in 90d should be flagged as coldstart."""
        result = self._compute_score(
            signal_count_30d=2,
            signal_count_90d=2,
            balance_usd=100_000,
            wallet_age_days=180,
        )
        self.assertTrue(result["is_coldstart"])

    def test_score_bounds(self):
        """Score should always be between 0 and 1."""
        test_cases = [
            {"signal_count_30d": 0, "signal_count_90d": 0, "balance_usd": 0},
            {"signal_count_30d": 1000, "signal_count_90d": 5000, "balance_usd": 1e12},
            {"signal_count_30d": 5, "signal_count_90d": 10, "balance_usd": 100},
        ]
        for tc in test_cases:
            result = self._compute_score(**tc)
            self.assertGreaterEqual(result["score"], 0.0, f"Score below 0 for {tc}")
            self.assertLessEqual(result["score"], 1.0, f"Score above 1 for {tc}")

    def test_execution_rate_effect(self):
        """Higher execution rate should increase reliability and score."""
        low = self._compute_score(
            signal_count_30d=20, signal_count_90d=40,
            execution_rate_30d=0.1, execution_rate_90d=0.1,
            balance_usd=500_000, wallet_age_days=365,
        )
        high = self._compute_score(
            signal_count_30d=20, signal_count_90d=40,
            execution_rate_30d=0.95, execution_rate_90d=0.9,
            balance_usd=500_000, wallet_age_days=365,
        )
        self.assertGreater(high["s_reliability"], low["s_reliability"])


class TestSignalThresholds(unittest.TestCase):
    """Test the USD threshold logic in signal_generator."""

    MIN_SIGNAL_USD_BY_CHAIN = {
        "btc": 10000.0,
        "eth": 5000.0,
        "sol": 2000.0,
    }
    MIN_SIGNAL_USD_DEFAULT = 5000.0

    def _meets_threshold(self, chain: str, amount_usd: float) -> bool:
        """Replicate threshold check from evaluate_for_signal."""
        _min = self.MIN_SIGNAL_USD_BY_CHAIN.get(chain.lower(), self.MIN_SIGNAL_USD_DEFAULT)
        return amount_usd >= _min

    def test_btc_above_threshold(self):
        self.assertTrue(self._meets_threshold("btc", 15000.0))

    def test_btc_below_threshold(self):
        self.assertFalse(self._meets_threshold("btc", 5000.0))

    def test_eth_at_threshold(self):
        self.assertTrue(self._meets_threshold("eth", 5000.0))

    def test_sol_below_threshold(self):
        self.assertFalse(self._meets_threshold("sol", 1000.0))

    def test_unknown_chain_uses_default(self):
        """Unknown chain should use default $5000 threshold."""
        self.assertTrue(self._meets_threshold("doge", 6000.0))
        self.assertFalse(self._meets_threshold("doge", 4000.0))


class TestSignalExplanation(unittest.TestCase):
    """Test the signal explanation template selection."""

    def _select_template(
        self,
        action: str,
        amount_usd: float,
        whale_score: float,
        median_amount_30d: float,
        execution_rate_30d: float = 0.0,
    ) -> str:
        """Simplified version of generate_explanation template selection."""
        # Confidence tiers (log-scaled from $1k to $1M)
        raw = math.log10(max(amount_usd, 1000)) - 3
        confidence = round(min(0.5 + (raw / 3) * 0.5, 1.0), 2)
        c_tx = confidence
        c_final = round(0.5 * c_tx + 0.5 * whale_score, 2)

        is_new = whale_score < 0.5
        is_proven = not is_new

        if median_amount_30d and median_amount_30d > 0:
            ratio = amount_usd / median_amount_30d
            if ratio >= 2.0:
                size_bucket = "large"
            elif ratio >= 0.5:
                size_bucket = "med"
            else:
                size_bucket = "small"
        else:
            size_bucket = "med"

        if c_final >= 0.75:
            conf_tier = "high"
        elif c_final >= 0.55:
            conf_tier = "med"
        else:
            conf_tier = "low"

        if action == "receive" and is_new:
            return "tpl_a"
        elif action == "receive" and is_proven and size_bucket == "large":
            return "tpl_b"
        elif action == "receive" and is_proven:
            return "tpl_c"
        elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "large":
            return "tpl_d"
        elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "med":
            return "tpl_e"
        elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "small":
            return "tpl_f"
        elif action == "buy" and is_proven:
            return "tpl_g"
        elif action == "buy" and is_new and conf_tier == "high":
            return "tpl_h"
        elif action == "buy" and is_new and conf_tier == "med":
            return "tpl_i"
        elif action == "buy" and is_new and conf_tier == "low" and size_bucket == "small":
            return "tpl_j"
        elif action == "buy" and is_new and conf_tier == "low":
            return "tpl_k"
        else:
            return "tpl_z"

    def test_receive_new_whale_selects_tpl_a(self):
        tpl = self._select_template("receive", 50000.0, whale_score=0.3, median_amount_30d=10000.0)
        self.assertEqual(tpl, "tpl_a")

    def test_receive_proven_large_selects_tpl_b(self):
        tpl = self._select_template("receive", 50000.0, whale_score=0.8, median_amount_30d=10000.0)
        self.assertEqual(tpl, "tpl_b")

    def test_buy_proven_high_large_selects_tpl_d(self):
        tpl = self._select_template("buy", 200000.0, whale_score=0.8, median_amount_30d=10000.0)
        self.assertEqual(tpl, "tpl_d")

    def test_buy_new_mid_confidence_selects_tpl_i(self):
        """Buy + new whale + mid confidence (c_final blended with low whale score) → tpl_i.
        With whale_score < 0.5, c_final = 0.5*c_tx + 0.5*w. Even with c_tx=1.0 and w=0.49,
        c_final=0.745 which is just under the 0.75 high threshold, so med tier wins."""
        tpl = self._select_template("buy", 500000.0, whale_score=0.3, median_amount_30d=0.0)
        self.assertEqual(tpl, "tpl_i")

    def test_buy_new_low_confidence_small_selects_tpl_j(self):
        """Buy + new whale + low confidence + small size → tpl_j."""
        tpl = self._select_template("buy", 2000.0, whale_score=0.3, median_amount_30d=10000.0)
        self.assertEqual(tpl, "tpl_j")

    def test_fallback_tpl_z_for_send_action(self):
        tpl = self._select_template("send", 50000.0, whale_score=0.8, median_amount_30d=10000.0)
        self.assertEqual(tpl, "tpl_z")


class TestSignalStaleExpiry(unittest.TestCase):
    """Test the stale signal expiry configuration constants."""

    def test_stale_threshold_positive(self):
        """SIGNAL_STALE_THRESHOLD_HOURS should be a positive integer."""
        # This constant is defined in monitor.py
        from services import monitor
        self.assertGreater(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 0)
        self.assertIsInstance(monitor.SIGNAL_STALE_THRESHOLD_HOURS, int)

    def test_stale_expiry_interval_positive(self):
        """STALE_EXPIRY_INTERVAL_CYCLES should be a positive integer."""
        from services import monitor
        self.assertGreater(monitor.STALE_EXPIRY_INTERVAL_CYCLES, 0)
        self.assertIsInstance(monitor.STALE_EXPIRY_INTERVAL_CYCLES, int)

    def test_stale_threshold_reasonable(self):
        """72 hours (3 days) is a reasonable default — between 1h and 30 days."""
        from services import monitor
        self.assertGreaterEqual(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 1)
        self.assertLessEqual(monitor.SIGNAL_STALE_THRESHOLD_HOURS, 720)  # 30 days max


class TestSignalDedup(unittest.TestCase):
    """Test the signal deduplication cache logic."""

    def setUp(self):
        """Reset the dedup cache before each test."""
        from services.signal_generator import _signal_dedup_cache, _mark_signal, _is_duplicate
        _signal_dedup_cache.clear()
        self._mark_signal = _mark_signal
        self._is_duplicate = _is_duplicate

    def test_not_duplicate_initially(self):
        self.assertFalse(self._is_duplicate("w1", "ETH", "buy"))

    def test_marked_as_duplicate(self):
        self._mark_signal("w1", "ETH", "buy")
        self.assertTrue(self._is_duplicate("w1", "ETH", "buy"))

    def test_different_wallet_not_duplicate(self):
        self._mark_signal("w1", "ETH", "buy")
        self.assertFalse(self._is_duplicate("w2", "ETH", "buy"))

    def test_different_token_not_duplicate(self):
        self._mark_signal("w1", "ETH", "buy")
        self.assertFalse(self._is_duplicate("w1", "BTC", "buy"))

    def test_case_insensitive_token(self):
        self._mark_signal("w1", "ETH", "buy")
        self.assertTrue(self._is_duplicate("w1", "eth", "buy"))


class TestConfidenceFormula(unittest.TestCase):
    """Test the confidence score computation: C_tx and C_final."""

    def _compute_confidence(self, amount_usd: float) -> float:
        raw = math.log10(max(amount_usd, 1000)) - 3
        return round(min(0.5 + (raw / 3) * 0.5, 1.0), 2)

    def _compute_c_final(self, c_tx: float, whale_score: float) -> float:
        return round(0.5 * c_tx + 0.5 * whale_score, 2)

    def test_minimum_confidence_at_1k(self):
        """$1000 tx should give minimum confidence of 0.5."""
        self.assertAlmostEqual(self._compute_confidence(1000), 0.5)

    def test_maximum_confidence_at_1m(self):
        """$1M tx should give maximum confidence of 1.0."""
        self.assertAlmostEqual(self._compute_confidence(1_000_000), 1.0)

    def test_mid_confidence(self):
        """$100K tx should give a mid-range confidence."""
        c = self._compute_confidence(100_000)
        self.assertGreater(c, 0.5)
        self.assertLess(c, 1.0)

    def test_c_final_is_blend(self):
        """C_final should be the average of C_tx and whale score."""
        c_tx = 0.8
        w = 0.6
        c_final = self._compute_c_final(c_tx, w)
        self.assertAlmostEqual(c_final, 0.7)

    def test_c_final_bounds(self):
        """C_final should be between 0 and 1."""
        for c_tx in [0.0, 0.5, 1.0]:
            for w in [0.0, 0.5, 1.0]:
                c_final = self._compute_c_final(c_tx, w)
                self.assertGreaterEqual(c_final, 0.0)
                self.assertLessEqual(c_final, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
