#!/usr/bin/env python3
"""
Unit tests for the mirror trade rate limiter (_check_mirror_rate_limit).

Tests cover:
- Token refill over time
- Burst exhaustion → 429 response
- Per-user isolation (one user's limit doesn't affect another)
- Token bucket initialization (new user gets burst tokens)
- Rate limiter resets after window passes
- Edge cases: zero tokens, fractional refill, max cap

Run: python3 -m pytest tests/test_mirror_rate_limit.py -v
"""
import asyncio
import sys
import os
import time as _real_time
import unittest
from unittest.mock import patch

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Set JWT_SECRET before importing main (main.py raises RuntimeError at module level
# if JWT_SECRET is not set, but the rate limiter functions we test don't need it).
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-only")

from main import (
    _check_mirror_rate_limit,
    _mirror_rate_buckets,
    _MIRROR_RATE_LIMIT,
    _MIRROR_RATE_WINDOW,
    _MIRROR_RATE_BURST,
)


class TestMirrorRateLimitInit(unittest.TestCase):
    """Test that a new user starts with burst allowance tokens."""

    def setUp(self):
        """Clear the rate bucket state before each test."""
        _mirror_rate_buckets.clear()

    def test_new_user_gets_burst_tokens(self):
        """First call for a new user should initialize with _MIRROR_RATE_BURST tokens."""
        _check_mirror_rate_limit("user_1")
        tokens, _ = _mirror_rate_buckets["user_1"]
        self.assertEqual(tokens, float(_MIRROR_RATE_BURST) - 1.0)

    def test_initial_tokens_equal_burst_minus_one(self):
        """After one request, tokens should be BURST - 1."""
        _check_mirror_rate_limit("user_1")
        tokens, _ = _mirror_rate_buckets["user_1"]
        self.assertAlmostEqual(tokens, float(_MIRROR_RATE_BURST) - 1.0)

    def test_burst_value_is_five(self):
        """The burst allowance should be 5."""
        self.assertEqual(_MIRROR_RATE_BURST, 5)

    def test_rate_limit_value_is_ten(self):
        """Max requests per window should be 10."""
        self.assertEqual(_MIRROR_RATE_LIMIT, 10)

    def test_window_is_60_seconds(self):
        """Rate limit window should be 60 seconds."""
        self.assertEqual(_MIRROR_RATE_WINDOW, 60.0)


class TestMirrorRateLimitBurstExhaustion(unittest.TestCase):
    """Test that burst exhaustion correctly denies requests."""

    def setUp(self):
        _mirror_rate_buckets.clear()

    def test_burst_exhaustion_returns_false(self):
        """After consuming all burst tokens, the next request should be denied."""
        user = "user_burst"
        # Consume all burst tokens (5 tokens, 5 calls)
        for _ in range(_MIRROR_RATE_BURST):
            result = _check_mirror_rate_limit(user)
            self.assertTrue(result, "Should allow initial burst requests")

        # 6th request should be denied (burst exhausted, no refill yet)
        result = _check_mirror_rate_limit(user)
        self.assertFalse(result, "Should deny request after burst exhaustion")

    def test_burst_plus_refill_allows_more(self):
        """After burst exhaustion, manually advancing the bucket timestamp should allow refill."""
        user = "user_refill"
        # Exhaust burst
        for _ in range(_MIRROR_RATE_BURST + 1):
            _check_mirror_rate_limit(user)

        # Now the bucket should have < 1 token (denied)
        tokens_before, _ = _mirror_rate_buckets[user]
        self.assertLess(tokens_before, 1.0)

        # Manually set the bucket's last_refill to 30 seconds ago to simulate time passing
        # Rate = 10/60 = 1 token per 6 seconds → 30 seconds = 5 tokens refilled
        current_time = _real_time.monotonic()
        _mirror_rate_buckets[user] = (0.0, current_time - 30.0)

        # Now the rate limiter should refill and allow a request
        result = _check_mirror_rate_limit(user)
        self.assertTrue(result, "Should allow request after refill period")

    def test_exactly_burst_requests_allowed(self):
        """Exactly BURST requests should be allowed in rapid succession."""
        user = "user_exact"
        results = [_check_mirror_rate_limit(user) for _ in range(_MIRROR_RATE_BURST)]
        self.assertTrue(all(results), f"All {_MIRROR_RATE_BURST} burst requests should be allowed")


class TestMirrorRateLimitPerUserIsolation(unittest.TestCase):
    """Test that rate limits are isolated per user."""

    def setUp(self):
        _mirror_rate_buckets.clear()

    def test_user_a_exhaustion_does_not_affect_user_b(self):
        """Exhausting user_a's limit should not affect user_b."""
        user_a = "user_a"
        user_b = "user_b"

        # Exhaust user_a's burst
        for _ in range(_MIRROR_RATE_BURST + 1):
            _check_mirror_rate_limit(user_a)

        # user_a should now be rate limited
        self.assertFalse(_check_mirror_rate_limit(user_a))

        # user_b should still have full burst available
        for _ in range(_MIRROR_RATE_BURST):
            result = _check_mirror_rate_limit(user_b)
            self.assertTrue(result, "user_b should not be affected by user_a's rate limit")

    def test_independent_buckets(self):
        """Each user should have an independent token bucket."""
        users = ["alice", "bob", "charlie"]
        for u in users:
            _check_mirror_rate_limit(u)

        # Each should have BURST - 1 tokens remaining
        for u in users:
            tokens, _ = _mirror_rate_buckets[u]
            self.assertAlmostEqual(tokens, float(_MIRROR_RATE_BURST) - 1.0)

    def test_many_users_isolation(self):
        """100 users should each have independent limits."""
        for i in range(100):
            _check_mirror_rate_limit(f"user_{i}")

        self.assertEqual(len(_mirror_rate_buckets), 100)


class TestMirrorRateLimitTokenRefill(unittest.TestCase):
    """Test the token refill logic over time."""

    def setUp(self):
        _mirror_rate_buckets.clear()

    def test_tokens_refill_over_time(self):
        """Tokens should refill at the configured rate (10 per 60 seconds)."""
        user = "refill_user"

        # Consume all burst tokens
        for _ in range(_MIRROR_RATE_BURST):
            _check_mirror_rate_limit(user)

        # Manually set the bucket to simulate time passing
        # Rate = 10 tokens / 60 seconds = 1 token per 6 seconds
        # After 30 seconds, should have 5 tokens refilled
        current_time = _real_time.monotonic()
        _mirror_rate_buckets[user] = (0.0, current_time - 30.0)

        # Now call the rate limiter — it should refill ~5 tokens
        result = _check_mirror_rate_limit(user)
        self.assertTrue(result, "Should allow request after 30 seconds of refill time")

    def test_refill_capped_at_burst(self):
        """Tokens should never exceed the burst cap, even after long wait."""
        user = "cap_user"
        current_time = _real_time.monotonic()

        # Set bucket to 0 tokens but with a very long elapsed time
        _mirror_rate_buckets[user] = (0.0, current_time - 10000.0)

        _check_mirror_rate_limit(user)
        tokens, _ = _mirror_rate_buckets[user]

        # Should be capped at BURST - 1 (one consumed), not more
        self.assertLessEqual(tokens, float(_MIRROR_RATE_BURST))

    def test_partial_refill(self):
        """Partial time should partially refill tokens."""
        user = "partial_user"
        current_time = _real_time.monotonic()

        # Set bucket to 0 tokens, 6 seconds ago → should refill ~1 token
        _mirror_rate_buckets[user] = (0.0, current_time - 6.0)

        result = _check_mirror_rate_limit(user)
        self.assertTrue(result, "Should allow 1 request after 6 seconds (1 token refilled)")

    def test_no_refill_without_time(self):
        """Without time passing, no tokens should refill."""
        user = "no_refill_user"

        # Exhaust burst
        for _ in range(_MIRROR_RATE_BURST):
            _check_mirror_rate_limit(user)

        # Immediately try again — should be denied
        result = _check_mirror_rate_limit(user)
        self.assertFalse(result, "Should deny without time passing")


class TestMirrorRateLimitReturnValue(unittest.TestCase):
    """Test that _check_mirror_rate_limit returns correct boolean values."""

    def setUp(self):
        _mirror_rate_buckets.clear()

    def test_returns_true_when_allowed(self):
        """Should return True when request is allowed."""
        result = _check_mirror_rate_limit("allowed_user")
        self.assertIsInstance(result, bool)
        self.assertTrue(result)

    def test_returns_false_when_denied(self):
        """Should return False when request is denied."""
        user = "denied_user"
        # Exhaust
        for _ in range(_MIRROR_RATE_BURST + 5):
            _check_mirror_rate_limit(user)

        result = _check_mirror_rate_limit(user)
        self.assertIsInstance(result, bool)
        self.assertFalse(result)


class TestMirrorRateLimitBucketState(unittest.TestCase):
    """Test internal bucket state consistency."""

    def setUp(self):
        _mirror_rate_buckets.clear()

    def test_bucket_stores_tuple(self):
        """Each bucket entry should be a (tokens: float, last_refill_ts: float) tuple."""
        _check_mirror_rate_limit("tuple_user")
        entry = _mirror_rate_buckets["tuple_user"]
        self.assertIsInstance(entry, tuple)
        self.assertEqual(len(entry), 2)
        self.assertIsInstance(entry[0], float)
        self.assertIsInstance(entry[1], float)

    def test_timestamp_updates_on_each_call(self):
        """The last_refill timestamp should update on each call."""
        user = "ts_user"
        _check_mirror_rate_limit(user)
        _, ts1 = _mirror_rate_buckets[user]

        # Small sleep to ensure monotonic clock advances
        _real_time.sleep(0.01)
        _check_mirror_rate_limit(user)
        _, ts2 = _mirror_rate_buckets[user]

        self.assertGreater(ts2, ts1, "Timestamp should advance on each call")

    def test_tokens_decrement_on_each_allowed_call(self):
        """Tokens should decrease by 1.0 on each allowed call."""
        user = "dec_user"
        _check_mirror_rate_limit(user)
        tokens_after_first, _ = _mirror_rate_buckets[user]

        _check_mirror_rate_limit(user)
        tokens_after_second, _ = _mirror_rate_buckets[user]

        self.assertAlmostEqual(
            tokens_after_first - tokens_after_second,
            1.0,
            places=5,
            msg="Each call should consume exactly 1 token"
        )


class TestMirrorRateLimitEndpointIntegration(unittest.TestCase):
    """Test that the rate limiter is correctly wired into the mirror_trade endpoint."""

    def test_rate_limit_check_exists_in_endpoint(self):
        """The mirror_trade function should call _check_mirror_rate_limit."""
        import inspect
        from main import mirror_trade
        source = inspect.getsource(mirror_trade)
        self.assertIn("_check_mirror_rate_limit", source)

    def test_rate_limit_returns_429(self):
        """When rate limited, the endpoint should raise HTTPException with 429."""
        import inspect
        from main import mirror_trade
        source = inspect.getsource(mirror_trade)
        self.assertIn("429", source)

    def test_rate_limit_uses_user_id(self):
        """The rate limiter should be called with the user's ID."""
        import inspect
        from main import mirror_trade
        source = inspect.getsource(mirror_trade)
        # Should pass user["id"] to the rate limiter
        self.assertIn('user["id"]', source)


class TestMirrorRateLimitConfig(unittest.TestCase):
    """Test that rate limit configuration values are sane."""

    def test_burst_lte_rate_limit(self):
        """Burst should not exceed the rate limit per window."""
        self.assertLessEqual(_MIRROR_RATE_BURST, _MIRROR_RATE_LIMIT)

    def test_window_is_positive(self):
        """Window should be a positive number."""
        self.assertGreater(_MIRROR_RATE_WINDOW, 0)

    def test_rate_is_positive(self):
        """Rate limit should be a positive number."""
        self.assertGreater(_MIRROR_RATE_LIMIT, 0)

    def test_burst_is_positive(self):
        """Burst should be a positive number."""
        self.assertGreater(_MIRROR_RATE_BURST, 0)

    def test_refill_rate_correct(self):
        """Refill rate should be tokens per second = LIMIT / WINDOW."""
        expected_rate = _MIRROR_RATE_LIMIT / _MIRROR_RATE_WINDOW
        self.assertAlmostEqual(expected_rate, 10.0 / 60.0, places=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
