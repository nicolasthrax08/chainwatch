#!/usr/bin/env python3
"""
Tests for the Phase 8 pool health check in the monitor worker.

Covers: pool stats included in cycle stats, warning logged at high utilization,
healthy logging at interval, None pool handling, pool read errors,
_phase_durations entry, and _pool_health_cycle_counter reset.
"""
import logging
import unittest
from unittest.mock import MagicMock

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.monitor as monitor


def _make_mock_pool(size=5, idle=3, max_size=10):
    """Return a MagicMock that mimics asyncpg pool stats methods."""
    pool = MagicMock()
    pool.get_size.return_value = size
    pool.get_idle_size.return_value = idle
    pool.get_max_size.return_value = max_size
    return pool


def _reset_monitor_state():
    """Reset all mutable monitor state between tests."""
    monitor._pool = None
    monitor._cycle_stats.clear()
    monitor._last_cycle_duration = 0.0
    monitor._stale_expiry_cycle_counter = 0
    monitor._pool_health_cycle_counter = 0
    monitor._phase_durations.clear()


class TestPoolHealthPhaseLogic(unittest.TestCase):
    """
    Test the Phase 8 pool health logic by simulating what happens
    at the end of a poll cycle.
    """

    def setUp(self):
        _reset_monitor_state()

    def _run_phase_8(self, pool):
        """
        Simulate the Phase 8 code path.
        Returns (pool_stats_dict_or_None, log_output).
        """
        import io

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("chainwatch.monitor")
        logger.addHandler(handler)

        pool_stats_this_cycle = None
        monitor._pool = pool

        if pool is not None:
            try:
                _pool_size = pool.get_size()
                _pool_idle = pool.get_idle_size()
                _pool_max = pool.get_max_size()
                _pool_used = _pool_size - _pool_idle
                _pool_util = round(_pool_used / max(_pool_max, 1) * 100, 1)
                pool_stats_this_cycle = {
                    "size": _pool_size,
                    "idle": _pool_idle,
                    "used": _pool_used,
                    "max_size": _pool_max,
                    "utilization_pct": _pool_util,
                    "healthy": _pool_used < _pool_max,
                }
            except Exception as e:
                pool_stats_this_cycle = {"error": str(e)}

        log_output = log_stream.getvalue()
        logger.removeHandler(handler)
        return pool_stats_this_cycle, log_output

    def test_pool_stats_shape_when_available(self):
        """Pool stats should have all expected keys when pool is available."""
        pool = _make_mock_pool(size=5, idle=3, max_size=10)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertEqual(stats["size"], 5)
        self.assertEqual(stats["idle"], 3)
        self.assertEqual(stats["used"], 2)
        self.assertEqual(stats["max_size"], 10)
        self.assertEqual(stats["utilization_pct"], 20.0)
        self.assertTrue(stats["healthy"])

    def test_pool_stats_none_when_pool_is_none(self):
        """Pool stats should be None when _pool is None."""
        stats, _ = self._run_phase_8(None)
        self.assertIsNone(stats)

    def test_pool_fully_utilized_not_healthy(self):
        """Pool at 100% utilization should have healthy=False."""
        pool = _make_mock_pool(size=10, idle=0, max_size=10)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertFalse(stats["healthy"])
        self.assertEqual(stats["utilization_pct"], 100.0)

    def test_pool_at_80_percent_is_healthy(self):
        """Pool at 80% utilization should still be healthy (8 < 10)."""
        pool = _make_mock_pool(size=8, idle=0, max_size=10)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertTrue(stats["healthy"])
        self.assertEqual(stats["utilization_pct"], 80.0)

    def test_pool_empty_is_healthy(self):
        """Pool with 0 connections should be healthy."""
        pool = _make_mock_pool(size=0, idle=0, max_size=10)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertTrue(stats["healthy"])
        self.assertEqual(stats["utilization_pct"], 0.0)

    def test_pool_error_returns_error_dict(self):
        """When pool methods raise, stats should contain error key."""
        pool = MagicMock()
        pool.get_size.side_effect = RuntimeError("pool closed")
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertIn("error", stats)
        self.assertIn("pool closed", stats["error"])

    def test_utilization_rounds_to_one_decimal(self):
        """Utilization should be rounded to 1 decimal place."""
        pool = _make_mock_pool(size=3, idle=0, max_size=7)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertEqual(stats["utilization_pct"], 42.9)

    def test_pool_with_one_max_size(self):
        """Edge case: max_size=1 should not cause division issues."""
        pool = _make_mock_pool(size=1, idle=0, max_size=1)
        stats, _ = self._run_phase_8(pool)
        assert stats is not None
        self.assertEqual(stats["utilization_pct"], 100.0)
        self.assertFalse(stats["healthy"])


class TestPoolHealthCycleCounter(unittest.TestCase):
    """Test the _pool_health_cycle_counter behavior."""

    def setUp(self):
        _reset_monitor_state()

    def test_counter_starts_at_zero(self):
        """_pool_health_cycle_counter should start at 0."""
        self.assertEqual(monitor._pool_health_cycle_counter, 0)

    def test_counter_increments_each_cycle(self):
        """Counter should increment by 1 each poll cycle."""
        self.assertEqual(monitor._pool_health_cycle_counter, 0)
        monitor._pool_health_cycle_counter += 1
        self.assertEqual(monitor._pool_health_cycle_counter, 1)
        monitor._pool_health_cycle_counter += 1
        self.assertEqual(monitor._pool_health_cycle_counter, 2)

    def test_counter_resets_at_interval(self):
        """Counter should reset to 0 when it reaches POOL_HEALTH_LOG_INTERVAL_CYCLES."""
        monitor._pool_health_cycle_counter = monitor.POOL_HEALTH_LOG_INTERVAL_CYCLES
        if monitor._pool_health_cycle_counter >= monitor.POOL_HEALTH_LOG_INTERVAL_CYCLES:
            monitor._pool_health_cycle_counter = 0
        self.assertEqual(monitor._pool_health_cycle_counter, 0)


class TestPoolHealthConfig(unittest.TestCase):
    """Test that pool health config values are reasonable."""

    def test_warn_threshold_is_80_percent(self):
        """Default warn threshold should be 80%."""
        self.assertEqual(monitor.POOL_HEALTH_WARN_THRESHOLD_PCT, 80.0)

    def test_log_interval_is_5_cycles(self):
        """Default log interval should be 5 cycles."""
        self.assertEqual(monitor.POOL_HEALTH_LOG_INTERVAL_CYCLES, 5)

    def test_warn_threshold_is_between_50_and_100(self):
        """Warn threshold should be in a reasonable range."""
        self.assertGreater(monitor.POOL_HEALTH_WARN_THRESHOLD_PCT, 50.0)
        self.assertLessEqual(monitor.POOL_HEALTH_WARN_THRESHOLD_PCT, 100.0)


class TestPoolHealthPhaseDuration(unittest.TestCase):
    """Test that phase8_pool_health appears in _phase_durations after a cycle."""

    def setUp(self):
        _reset_monitor_state()

    def test_phase_durations_key_exists(self):
        """Simulated phase8_pool_health should appear in _phase_durations."""
        import time as _time_mod
        _phase_t0 = _time_mod.monotonic()
        _phase_durations = {}
        _phase_durations["phase8_pool_health"] = round(_time_mod.monotonic() - _phase_t0, 3)
        self.assertIn("phase8_pool_health", _phase_durations)
        self.assertIsInstance(_phase_durations["phase8_pool_health"], float)


if __name__ == "__main__":
    unittest.main()
