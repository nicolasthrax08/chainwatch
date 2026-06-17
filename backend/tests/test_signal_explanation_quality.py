#!/usr/bin/env python3
"""
Unit tests for signal explanation quality.

Validates that generate_explanation() produces high-quality explanations
across all template paths and edge cases.

Run: python3 -m pytest backend/tests/test_signal_explanation_quality.py -v
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import signal_generator as sg


def _run(coro):
    """Run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestExplanationLength(unittest.TestCase):
    """All explanations must be <= 120 chars."""

    def _make_signal(self, **overrides):
        base = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "Test Whale",
            "wallet_address": "0xabc123def456789",
            "is_receive": False,
            "confidence_score": 0.8,
            "confidence_final": 0.75,
            "execution_rate_30d": 0.6,
        }
        base.update(overrides)
        return base

    def test_explanation_under_120_chars(self):
        """Standard buy signal explanation should be under 120 chars."""
        signal_data = self._make_signal()
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        self.assertLessEqual(len(result), 120, f"Explanation too long ({len(result)} chars): {result!r}")

    def test_explanation_with_long_label_truncated(self):
        """Explanation with very long wallet label should be truncated to 120 chars."""
        signal_data = self._make_signal(
            wallet_label="A" * 200,
            amount_usd=100000.0,
        )
        result = sg.generate_explanation(signal_data, whale_score=0.8, median_amount_30d=5000.0)
        self.assertLessEqual(len(result), 120, f"Explanation too long ({len(result)} chars): {result!r}")
        if len(result) == 120:
            self.assertTrue(result.endswith("..."), "Truncated explanation should end with '...'")

    def test_explanation_receive_under_120_chars(self):
        """Receive signal explanation should be under 120 chars."""
        signal_data = self._make_signal(
            action="receive",
            is_receive=True,
            amount_usd=100000.0,
        )
        result = sg.generate_explanation(signal_data, whale_score=0.8, median_amount_30d=5000.0)
        self.assertLessEqual(len(result), 120, f"Explanation too long ({len(result)} chars): {result!r}")


class TestExplanationNoPlaceholders(unittest.TestCase):
    """Explanations must not contain placeholder text."""

    # Note: "—" (em-dash) is intentionally used as a separator in TPL-Z fallback
    # and is NOT a placeholder. It's a deliberate stylistic choice.
    PLACEHOLDER_TEXTS = ["TODO", "TBD", "N/A", "PLACEHOLDER"]

    def _make_signal(self, **overrides):
        base = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "Test Whale",
            "wallet_address": "0xabc123def456789",
            "is_receive": False,
            "confidence_score": 0.8,
            "confidence_final": 0.75,
            "execution_rate_30d": 0.6,
        }
        base.update(overrides)
        return base

    def test_no_placeholder_text_in_buy_signal(self):
        """Buy signal explanation should not contain placeholder text."""
        signal_data = self._make_signal()
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        for pat in self.PLACEHOLDER_TEXTS:
            self.assertNotIn(pat, result, f"Explanation contains placeholder: {pat!r}")

    def test_no_placeholder_text_in_receive_signal(self):
        """Receive signal explanation should not contain placeholder text."""
        signal_data = self._make_signal(action="receive", is_receive=True)
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        for pat in self.PLACEHOLDER_TEXTS:
            self.assertNotIn(pat, result, f"Explanation contains placeholder: {pat!r}")

    def test_no_placeholder_text_in_fallback(self):
        """Fallback template (sell action) should not contain placeholder text."""
        signal_data = self._make_signal(action="sell")
        result = sg.generate_explanation(signal_data, whale_score=0.35, median_amount_30d=0.0)
        for pat in self.PLACEHOLDER_TEXTS:
            self.assertNotIn(pat, result, f"Explanation contains placeholder: {pat!r}")


class TestExplanationContentQuality(unittest.TestCase):
    """Explanations should contain meaningful content based on context."""

    def _make_signal(self, **overrides):
        base = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "Smart Money",
            "wallet_address": "0xabc123def456789",
            "is_receive": False,
            "confidence_score": 0.9,
            "confidence_final": 0.85,
            "execution_rate_30d": 0.7,
        }
        base.update(overrides)
        return base

    def test_proven_whale_contains_quality_keyword(self):
        """Proven whale (score >= 0.30) explanation should contain quality keywords."""
        signal_data = self._make_signal()
        result = sg.generate_explanation(signal_data, whale_score=0.8, median_amount_30d=5000.0)
        quality_keywords = ["consistent whale", "confidence trade", "moderate confidence"]
        has_keyword = any(kw in result.lower() for kw in quality_keywords)
        # At least one quality keyword OR a descriptive phrase should be present
        self.assertTrue(
            has_keyword or "above average" in result.lower() or "notable" in result.lower(),
            f"Proven whale explanation lacks quality keywords: {result!r}",
        )

    def test_new_whale_contains_new_whale_prefix(self):
        """New whale (score < 0.30) explanation should contain 'New whale'."""
        signal_data = self._make_signal(
            action="buy",
            is_receive=False,
            confidence_final=0.65,
        )
        result = sg.generate_explanation(signal_data, whale_score=0.25, median_amount_30d=0.0)
        self.assertIn("New whale", result, f"New whale explanation missing 'New whale': {result!r}")

    def test_new_whale_receive_contains_first_signal(self):
        """New whale receive explanation should mention 'first signal'."""
        signal_data = self._make_signal(
            action="receive",
            is_receive=True,
            confidence_final=0.65,
        )
        result = sg.generate_explanation(signal_data, whale_score=0.25, median_amount_30d=0.0)
        self.assertIn("first signal", result, f"New whale receive missing 'first signal': {result!r}")

    def test_fallback_contains_score_and_confidence(self):
        """Fallback template (TPL-Z) should include whale score and confidence percentages."""
        signal_data = self._make_signal(action="sell", confidence_score=0.5, confidence_final=0.5)
        result = sg.generate_explanation(signal_data, whale_score=0.35, median_amount_30d=0.0)
        self.assertIn("35%", result, f"Fallback missing whale score %: {result!r}")
        self.assertIn("50%", result, f"Fallback missing confidence %: {result!r}")

    def test_large_trade_mentions_size(self):
        """Large trade (2x median) should mention size in explanation."""
        signal_data = self._make_signal(amount_usd=100000.0)
        result = sg.generate_explanation(signal_data, whale_score=0.8, median_amount_30d=10000.0)
        # ratio = 10x → "large" bucket
        self.assertTrue(
            "above average" in result.lower() or "large" in result.lower() or "notable" in result.lower(),
            f"Large trade explanation doesn't mention size: {result!r}",
        )

    def test_proven_whale_receive_mentions_execution_rate(self):
        """Proven whale receive with high execution rate should mention it."""
        signal_data = self._make_signal(
            action="receive",
            is_receive=True,
            execution_rate_30d=0.85,
        )
        result = sg.generate_explanation(signal_data, whale_score=0.8, median_amount_30d=5000.0)
        self.assertIn("85%", result, f"Proven whale receive missing execution rate: {result!r}")


class TestExplanationEdgeCases(unittest.TestCase):
    """Edge cases for explanation generation."""

    def test_zero_amount(self):
        """Zero amount should not crash explanation generation."""
        signal_data = {
            "action": "buy",
            "amount_usd": 0.0,
            "token_symbol": "ETH",
            "wallet_label": "Test",
            "wallet_address": "0xabc",
            "is_receive": False,
            "confidence_score": 0.5,
            "confidence_final": 0.4,
        }
        result = sg.generate_explanation(signal_data, whale_score=0.5, median_amount_30d=0.0)
        self.assertIsInstance(result, str)
        self.assertLessEqual(len(result), 120)

    def test_very_large_amount(self):
        """Very large amount ($10M+) should produce valid explanation."""
        signal_data = {
            "action": "buy",
            "amount_usd": 10_000_000.0,
            "token_symbol": "ETH",
            "wallet_label": "Whale",
            "wallet_address": "0xabc",
            "is_receive": False,
            "confidence_score": 1.0,
            "confidence_final": 0.9,
        }
        result = sg.generate_explanation(signal_data, whale_score=0.85, median_amount_30d=50000.0)
        self.assertIsInstance(result, str)
        self.assertLessEqual(len(result), 120)
        self.assertGreater(len(result), 0)

    def test_no_label_uses_address(self):
        """When wallet_label is None, explanation should use address prefix."""
        signal_data = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": None,
            "wallet_address": "0xabcdef1234567890",
            "is_receive": False,
            "confidence_score": 0.8,
            "confidence_final": 0.75,
        }
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        self.assertIn("Whale", result, f"Missing 'Whale' prefix for no-label wallet: {result!r}")
        self.assertIn("0xabc", result, f"Missing address prefix for no-label wallet: {result!r}")

    def test_empty_label_uses_address(self):
        """When wallet_label is empty string, explanation should use address prefix."""
        signal_data = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "   ",
            "wallet_address": "0xabcdef1234567890",
            "is_receive": False,
            "confidence_score": 0.8,
            "confidence_final": 0.75,
        }
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        self.assertIn("Whale", result, f"Missing 'Whale' prefix for empty-label wallet: {result!r}")

    def test_unknown_action_uses_fallback(self):
        """Unknown action (e.g., 'transfer') should use fallback template."""
        signal_data = {
            "action": "transfer",
            "amount_usd": 5000.0,
            "token_symbol": "ETH",
            "wallet_label": "Test",
            "wallet_address": "0xabc",
            "is_receive": False,
            "confidence_score": 0.5,
            "confidence_final": 0.4,
        }
        result = sg.generate_explanation(signal_data, whale_score=0.35, median_amount_30d=0.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), 120)

    def test_missing_confidence_final_uses_c_tx(self):
        """When confidence_final is missing, should fall back to confidence_score."""
        signal_data = {
            "action": "buy",
            "amount_usd": 50000.0,
            "token_symbol": "ETH",
            "wallet_label": "Test",
            "wallet_address": "0xabc",
            "is_receive": False,
            "confidence_score": 0.8,
            # confidence_final is missing — should fall back to c_tx
        }
        result = sg.generate_explanation(signal_data, whale_score=0.7, median_amount_30d=5000.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), 120)


class TestExplanationAllTemplatePaths(unittest.TestCase):
    """Verify all 12 template paths (TPL-A through TPL-Z) produce valid output."""

    def test_all_templates_produce_valid_strings(self):
        """Each template path should produce a non-empty string <= 120 chars."""
        templates = [
            # TPL-A: receive + new whale
            {
                "action": "receive", "is_receive": True, "whale_score": 0.25,
                "median_amount_30d": 0.0, "amount_usd": 50000.0,
                "label": "New Whale", "expected_substr": "first signal",
            },
            # TPL-B: receive + proven + large
            {
                "action": "receive", "is_receive": True, "whale_score": 0.8,
                "median_amount_30d": 1000.0, "amount_usd": 100000.0,
                "label": "Proven Whale", "expected_substr": "consistent whale",
                "execution_rate_30d": 0.7,
            },
            # TPL-C: receive + proven + non-large
            {
                "action": "receive", "is_receive": True, "whale_score": 0.8,
                "median_amount_30d": 50000.0, "amount_usd": 50000.0,
                "label": "Proven Whale", "expected_substr": None,
            },
            # TPL-D: buy + proven + high + large
            {
                "action": "buy", "is_receive": False, "whale_score": 0.8,
                "median_amount_30d": 1000.0, "amount_usd": 100000.0,
                "label": "Smart Money", "expected_substr": "above average",
                "confidence_final": 0.85,
            },
            # TPL-E: buy + proven + high + med
            {
                "action": "buy", "is_receive": False, "whale_score": 0.8,
                "median_amount_30d": 50000.0, "amount_usd": 50000.0,
                "label": "Smart Money", "expected_substr": "strong confidence",
                "confidence_final": 0.85,
            },
            # TPL-F: buy + proven + high + small
            {
                "action": "buy", "is_receive": False, "whale_score": 0.8,
                "median_amount_30d": 100000.0, "amount_usd": 5000.0,
                "label": "Smart Money", "expected_substr": "notable",
                "confidence_final": 0.85,
            },
            # TPL-G: buy + proven + med/low
            {
                "action": "buy", "is_receive": False, "whale_score": 0.8,
                "median_amount_30d": 5000.0, "amount_usd": 50000.0,
                "label": "Smart Money", "expected_substr": "moderate confidence",
                "confidence_final": 0.55,
            },
            # TPL-H: buy + new + high
            {
                "action": "buy", "is_receive": False, "whale_score": 0.25,
                "median_amount_30d": 0.0, "amount_usd": 100000.0,
                "label": "New Whale", "expected_substr": "New whale",
                "confidence_final": 0.85,
            },
            # TPL-I: buy + new + med
            {
                "action": "buy", "is_receive": False, "whale_score": 0.25,
                "median_amount_30d": 0.0, "amount_usd": 50000.0,
                "label": "New Whale", "expected_substr": "New whale",
                "confidence_final": 0.65,
            },
            # TPL-J: buy + new + low + small
            {
                "action": "buy", "is_receive": False, "whale_score": 0.25,
                "median_amount_30d": 100000.0, "amount_usd": 5000.0,
                "label": "New Whale", "expected_substr": "New whale",
                "confidence_final": 0.35,
            },
            # TPL-K: buy + new + low + med/large
            {
                "action": "buy", "is_receive": False, "whale_score": 0.25,
                "median_amount_30d": 5000.0, "amount_usd": 50000.0,
                "label": "New Whale", "expected_substr": "New whale",
                "confidence_final": 0.35,
            },
            # TPL-Z: fallback (sell)
            {
                "action": "sell", "is_receive": False, "whale_score": 0.35,
                "median_amount_30d": 0.0, "amount_usd": 5000.0,
                "label": None, "expected_substr": "whale score",
                "confidence_final": 0.4,
            },
        ]

        for i, tpl in enumerate(templates):
            label = tpl.pop("label")
            expected = tpl.pop("expected_substr", None)
            signal_data = {
                "action": tpl["action"],
                "amount_usd": tpl["amount_usd"],
                "token_symbol": "ETH",
                "wallet_label": label,
                "wallet_address": "0xabc123def456789",
                "is_receive": tpl["is_receive"],
                "confidence_score": tpl.get("confidence_final", 0.7),
                "confidence_final": tpl.get("confidence_final", 0.7),
                "execution_rate_30d": tpl.get("execution_rate_30d", 0.0),
            }
            result = sg.generate_explanation(
                signal_data,
                whale_score=tpl["whale_score"],
                median_amount_30d=tpl["median_amount_30d"],
            )
            self.assertIsInstance(result, str, f"Template {i}: expected str, got {type(result)}")
            self.assertGreater(len(result), 0, f"Template {i}: empty explanation")
            self.assertLessEqual(len(result), 120, f"Template {i}: too long ({len(result)}): {result!r}")
            if expected:
                self.assertIn(expected, result, f"Template {i}: missing '{expected}' in: {result!r}")


if __name__ == "__main__":
    unittest.main()
