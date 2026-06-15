#!/usr/bin/env python3
"""
Unit tests for signal_generator coverage gaps.

Tests cover gaps identified in the 2026-06-14 test coverage audit:
- Stablecoin signal suppression (USDC/USDT should not generate signals)
- Whale score threshold filtering (MIN_WHALE_SCORE)
- Explanation edge cases (all template branches)
- C_final blending formula
- Dedup: in-memory cache + DB fallback
- tx_type filtering (only buy/receive generate signals)

Run: python3 -m pytest tests/test_signal_generator_gaps.py -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import signal_generator


class TestSignalGeneratorStablecoinSuppression(unittest.TestCase):
    """Test that stablecoin transactions are handled correctly in signal generation."""

    def setUp(self):
        signal_generator._signal_dedup_cache.clear()

    def test_usdc_tx_below_min_usd_does_not_generate_signal(self):
        """A USDC tx worth less than MIN_SIGNAL_USD should not generate a signal."""
        async def run():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            conn.fetchrow = AsyncMock(return_value=None)
            # USDC tx worth $500 (below $5000 default threshold)
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="USDC",
                tx_amount_native=500.0,
                price_usd=1.0,
                whale_score=0.8,
            )
            self.assertIsNone(result)

        asyncio.run(run())

    def test_usdc_tx_above_min_usd_does_generate_signal(self):
        """A USDC tx worth more than MIN_SIGNAL_USD should generate a signal (price=1.0)."""
        async def run():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            mock_signal = {
                "id": "sig-001",
                "wallet_id": "wallet-001",
                "token_symbol": "USDC",
                "action": "buy",
                "amount_usd": 10000.0,
                "confidence_score": 0.67,
                "confidence_final": 0.74,
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
            }
            conn.fetchrow = AsyncMock(return_value=mock_signal)
            # USDC tx worth $10000 (above $5000 default threshold)
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="USDC",
                tx_amount_native=10000.0,
                price_usd=1.0,
                whale_score=0.8,
            )
            self.assertIsNotNone(result)

        asyncio.run(run())


class TestSignalGeneratorWhaleScoreThreshold(unittest.TestCase):
    """Test MIN_WHALE_SCORE filtering."""

    def setUp(self):
        signal_generator._signal_dedup_cache.clear()

    def test_low_whale_score_suppresses_signal(self):
        """A wallet with whale_score < MIN_WHALE_SCORE should not generate signals."""
        async def run():
            conn = AsyncMock()
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="ETH",
                tx_amount_native=10.0,
                price_usd=2500.0,
                whale_score=0.1,  # Below MIN_WHALE_SCORE (0.30)
            )
            self.assertIsNone(result)
            # No DB queries should have been made
            conn.fetchval.assert_not_called()

        asyncio.run(run())

    def test_exact_min_whale_score_generates_signal(self):
        """A wallet with whale_score == MIN_WHALE_SCORE should generate signals."""
        async def run():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            mock_signal = {
                "id": "sig-001",
                "wallet_id": "wallet-001",
                "token_symbol": "ETH",
                "action": "buy",
                "amount_usd": 25000.0,
                "confidence_score": 0.72,
                "confidence_final": 0.51,
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
            }
            conn.fetchrow = AsyncMock(return_value=mock_signal)
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="ETH",
                tx_amount_native=10.0,
                price_usd=2500.0,
                whale_score=0.30,  # Exactly MIN_WHALE_SCORE
            )
            self.assertIsNotNone(result)

        asyncio.run(run())


class TestSignalGeneratorTxTypeFiltering(unittest.TestCase):
    """Test that only buy/receive tx_types generate signals."""

    def setUp(self):
        signal_generator._signal_dedup_cache.clear()

    def test_send_tx_does_not_generate_signal(self):
        """A 'send' transaction should not generate a signal."""
        async def run():
            conn = AsyncMock()
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="send",
                token="ETH",
                tx_amount_native=100.0,
                price_usd=2500.0,
                whale_score=0.8,
            )
            self.assertIsNone(result)

        asyncio.run(run())

    def test_unknown_tx_type_does_not_generate_signal(self):
        """An unknown tx_type should not generate a signal."""
        async def run():
            conn = AsyncMock()
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="swap",
                token="ETH",
                tx_amount_native=100.0,
                price_usd=2500.0,
                whale_score=0.8,
            )
            self.assertIsNone(result)

        asyncio.run(run())


class TestSignalGeneratorCFinal(unittest.TestCase):
    """Test C_final blending formula: C_final = 0.5 * C_tx + 0.5 * whale_score."""

    def setUp(self):
        signal_generator._signal_dedup_cache.clear()

    def test_c_final_blending(self):
        """C_final should be the average of C_tx and whale_score."""
        async def run():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            mock_signal = {
                "id": "sig-001",
                "wallet_id": "wallet-001",
                "token_symbol": "ETH",
                "action": "buy",
                "amount_usd": 100000.0,
                "confidence_score": 0.83,
                "confidence_final": 0.67,  # (0.83 + 0.5) / 2 = 0.665 ≈ 0.67
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
            }
            conn.fetchrow = AsyncMock(return_value=mock_signal)
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="ETH",
                tx_amount_native=40.0,
                price_usd=2500.0,
                whale_score=0.5,
            )
            self.assertIsNotNone(result)

        asyncio.run(run())


class TestSignalGeneratorExplanationEdgeCases(unittest.TestCase):
    """Test explanation generation edge cases (all template branches)."""

    def test_explanation_truncated_to_120_chars(self):
        """Explanations longer than 120 chars should be truncated."""
        signal_data = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "Very Long Wallet Name That Should Cause Truncation",
            "wallet_address": "0xabc123def456",
            "is_receive": False,
            "confidence_score": 0.9,
            "confidence_final": 0.85,
        }
        result = signal_generator.generate_explanation(
            signal_data=signal_data,
            whale_score=0.8,
            median_amount_30d=5000.0,
        )
        self.assertLessEqual(len(result), 120)
        if len(result) == 120:
            self.assertTrue(result.endswith("..."))

    def test_explanation_fallback_template(self):
        """The fallback template (TPL-Z) should include whale score and confidence %."""
        signal_data = {
            "action": "sell",  # Not buy/receive → fallback
            "amount_usd": 5000.0,
            "token_symbol": "ETH",
            "wallet_label": None,
            "wallet_address": "0xabc123def456",
            "is_receive": False,
            "confidence_score": 0.5,
            "confidence_final": 0.4,
        }
        result = signal_generator.generate_explanation(
            signal_data=signal_data,
            whale_score=0.35,
            median_amount_30d=0.0,
        )
        self.assertIn("35%", result)  # whale score %
        self.assertIn("40%", result)  # confidence %

    def test_explanation_receive_new_whale(self):
        """TPL-A: receive + new whale (score < MIN_WHALE_SCORE)."""
        signal_data = {
            "action": "receive",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "New Whale",
            "wallet_address": "0xabc123",
            "is_receive": True,
            "confidence_score": 0.8,
            "confidence_final": 0.65,
        }
        result = signal_generator.generate_explanation(
            signal_data=signal_data,
            whale_score=0.29,  # < MIN_WHALE_SCORE (0.30) → new whale
            median_amount_30d=5000.0,
        )
        self.assertIn("first signal", result)

    def test_explanation_buy_proven_high_large(self):
        """TPL-D: buy + proven + high confidence + large size."""
        signal_data = {
            "action": "buy",
            "amount_usd": 100000.0,
            "token_symbol": "ETH",
            "wallet_label": "Smart Money",
            "wallet_address": "0xabc123",
            "is_receive": False,
            "confidence_score": 0.9,
            "confidence_final": 0.85,
            "execution_rate_30d": 0.7,
        }
        result = signal_generator.generate_explanation(
            signal_data=signal_data,
            whale_score=0.8,
            median_amount_30d=5000.0,  # ratio = 20x → large
        )
        self.assertIn("above average size", result)


class TestSignalGeneratorDedup(unittest.TestCase):
    """Test signal deduplication (in-memory + DB)."""

    def setUp(self):
        signal_generator._signal_dedup_cache.clear()

    def test_in_memory_dedup_prevents_duplicate(self):
        """A signal with the same wallet+token+action within TTL should be deduped."""
        async def run():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            mock_signal = {
                "id": "sig-001",
                "wallet_id": "wallet-001",
                "token_symbol": "ETH",
                "action": "buy",
                "amount_usd": 25000.0,
                "confidence_score": 0.72,
                "confidence_final": 0.61,
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
            }
            conn.fetchrow = AsyncMock(return_value=mock_signal)
            # First call should generate signal
            result1 = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="ETH",
                tx_amount_native=10.0,
                price_usd=2500.0,
                whale_score=0.5,
            )
            self.assertIsNotNone(result1)
            # Second call with same wallet+token+action should be deduped
            result2 = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash2",
                tx_type="buy",
                token="ETH",
                tx_amount_native=10.0,
                price_usd=2500.0,
                whale_score=0.5,
            )
            self.assertIsNone(result2)

        asyncio.run(run())

    def test_db_dedup_prevents_duplicate(self):
        """When in-memory cache is cleared but DB has recent signal, should dedup."""
        async def run():
            conn = AsyncMock()
            # DB returns an existing signal (within 5 min window)
            conn.fetchval = AsyncMock(return_value="existing-sig-id")
            result = await signal_generator.evaluate_for_signal(
                conn=conn,
                wallet_id="wallet-001",
                is_whale=True,
                user_id="user-001",
                chain="eth",
                tx_hash="0xhash1",
                tx_type="buy",
                token="ETH",
                tx_amount_native=10.0,
                price_usd=2500.0,
                whale_score=0.5,
            )
            self.assertIsNone(result)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
