#!/usr/bin/env python3
"""
Tests for the health_metrics.py service.
Covers: record_request, record_db_query, get_metrics, reset_metrics, _format_duration,
ring buffer behavior, latency percentile computation, and edge cases.
"""
import os
import sys
import time
import math

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services"))

import health_metrics as hm


@pytest.fixture(autouse=True)
def _reset():
    """Reset metrics before every test to ensure isolation."""
    hm.reset_metrics()
    # Small sleep so uptime is non-zero but tiny
    yield
    hm.reset_metrics()


# ── _format_duration ──────────────────────────────────────────────────

class TestFormatDuration:

    def test_seconds_range(self):
        assert hm._format_duration(0) == "0s"
        assert hm._format_duration(1) == "1s"
        assert hm._format_duration(30) == "30s"
        assert hm._format_duration(59) == "59s"

    def test_minutes_range(self):
        assert hm._format_duration(60) == "1.0m"
        assert hm._format_duration(90) == "1.5m"
        assert hm._format_duration(3599) == "60.0m"

    def test_hours_range(self):
        assert hm._format_duration(3600) == "1.0h"
        assert hm._format_duration(7200) == "2.0h"
        assert hm._format_duration(86399) == "24.0h"

    def test_days_range(self):
        assert hm._format_duration(86400) == "1.0d"
        assert hm._format_duration(172800) == "2.0d"
        assert hm._format_duration(259200) == "3.0d"

    def test_fractional_seconds(self):
        assert hm._format_duration(0.5) == "0s"
        assert hm._format_duration(45.7) == "46s"


# ── record_request ────────────────────────────────────────────────────

class TestRecordRequest:

    def test_increments_request_count(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert m["request_count"] == 1

    def test_multiple_requests(self):
        for _ in range(10):
            hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert m["request_count"] == 10

    def test_500_increments_error_count(self):
        hm.record_request("GET", "/api/test", 500, 0.01)
        m = hm.get_metrics()
        assert m["error_count"] == 1

    def test_5xx_variants(self):
        for status in [500, 502, 503, 504]:
            hm.reset_metrics()
            hm.record_request("GET", "/api/test", status, 0.01)
            m = hm.get_metrics()
            assert m["error_count"] == 1, f"status {status} should count as error"

    def test_400_not_error(self):
        hm.record_request("GET", "/api/test", 400, 0.01)
        m = hm.get_metrics()
        assert m["error_count"] == 0

    def test_200_not_error(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert m["error_count"] == 0

    def test_method_is_uppercased(self):
        hm.record_request("get", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert "GET /api/test" in m["endpoint_latency"]

    def test_latency_stored(self):
        hm.record_request("GET", "/api/test", 200, 0.123)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 1
        assert stats["avg_ms"] == 123.0

    def test_multiple_latencies_for_same_endpoint(self):
        for i in range(5):
            hm.record_request("GET", "/api/test", 200, 0.01 * (i + 1))
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 5

    def test_different_endpoints_tracked_separately(self):
        hm.record_request("GET", "/api/a", 200, 0.01)
        hm.record_request("POST", "/api/b", 200, 0.02)
        m = hm.get_metrics()
        assert "GET /api/a" in m["endpoint_latency"]
        assert "POST /api/b" in m["endpoint_latency"]


# ── Ring buffer (latency trimming) ────────────────────────────────────

class TestRingBuffer:

    def test_buffer_trims_at_100(self):
        """After 101 requests, buffer should be trimmed to 100 (oldest removed)."""
        for i in range(101):
            hm.record_request("GET", "/api/test", 200, 0.001)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 100

    def test_buffer_exactly_100(self):
        for i in range(100):
            hm.record_request("GET", "/api/test", 200, 0.001)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 100

    def test_buffer_99(self):
        for i in range(99):
            hm.record_request("GET", "/api/test", 200, 0.001)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 99


# ── record_db_query ───────────────────────────────────────────────────

class TestRecordDbQuery:

    def test_increments_query_count(self):
        hm.record_db_query(success=True)
        m = hm.get_metrics()
        assert m["db_query_count"] == 1

    def test_multiple_queries(self):
        for _ in range(5):
            hm.record_db_query(success=True)
        m = hm.get_metrics()
        assert m["db_query_count"] == 5

    def test_failed_query_increments_error_count(self):
        hm.record_db_query(success=False)
        m = hm.get_metrics()
        assert m["db_error_count"] == 1
        assert m["db_query_count"] == 1

    def test_success_does_not_increment_error(self):
        hm.record_db_query(success=True)
        m = hm.get_metrics()
        assert m["db_error_count"] == 0

    def test_mixed_success_and_failure(self):
        hm.record_db_query(success=True)
        hm.record_db_query(success=True)
        hm.record_db_query(success=False)
        hm.record_db_query(success=True)
        m = hm.get_metrics()
        assert m["db_query_count"] == 4
        assert m["db_error_count"] == 1


# ── get_metrics ───────────────────────────────────────────────────────

class TestGetMetrics:

    def test_returns_all_expected_keys(self):
        m = hm.get_metrics()
        expected_keys = {
            "uptime_seconds", "uptime_human", "request_count", "error_count",
            "error_rate", "db_query_count", "db_error_count", "db_error_rate",
            "endpoint_latency",
        }
        assert set(m.keys()) == expected_keys

    def test_uptime_nonzero(self):
        time.sleep(0.05)
        m = hm.get_metrics()
        assert m["uptime_seconds"] >= 0.05

    def test_uptime_human_is_string(self):
        m = hm.get_metrics()
        assert isinstance(m["uptime_human"], str)

    def test_error_rate_zero_when_no_requests(self):
        m = hm.get_metrics()
        assert m["error_rate"] == 0.0

    def test_error_rate_calculation(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.record_request("GET", "/api/test", 500, 0.01)
        hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert m["error_rate"] == 25.0  # 1 of 4

    def test_error_rate_100(self):
        hm.record_request("GET", "/api/test", 500, 0.01)
        m = hm.get_metrics()
        assert m["error_rate"] == 100.0

    def test_db_error_rate_zero_when_no_queries(self):
        m = hm.get_metrics()
        assert m["db_error_rate"] == 0.0

    def test_db_error_rate_calculation(self):
        hm.record_db_query(success=True)
        hm.record_db_query(success=True)
        hm.record_db_query(success=False)
        m = hm.get_metrics()
        assert m["db_error_rate"] == pytest.approx(33.33, abs=0.01)

    def test_endpoint_latency_empty_when_no_requests(self):
        m = hm.get_metrics()
        assert m["endpoint_latency"] == {}

    def test_endpoint_latency_shape(self):
        hm.record_request("GET", "/api/test", 200, 0.05)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert "count" in stats
        assert "p50_ms" in stats
        assert "p95_ms" in stats
        assert "p99_ms" in stats
        assert "avg_ms" in stats

    def test_p95_none_with_few_samples(self):
        """p95 is None when count < 20."""
        for _ in range(5):
            hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["p95_ms"] is None

    def test_p95_present_with_20_samples(self):
        """p95 is computed when count >= 20."""
        for _ in range(20):
            hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["p95_ms"] is not None

    def test_p99_none_with_few_samples(self):
        """p99 is None when count < 100."""
        for _ in range(50):
            hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["p99_ms"] is None

    def test_p99_present_with_100_samples(self):
        """p99 is computed when count >= 100."""
        for _ in range(100):
            hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["p99_ms"] is not None

    def test_p50_correct_value(self):
        """p50 should be the median of sorted samples."""
        for i in range(1, 11):
            hm.record_request("GET", "/api/test", 200, i * 0.001)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        # Sorted: 1,2,3,4,5,6,7,8,9,10 ms → p50 at index 5 → 6ms
        assert stats["p50_ms"] == 6.0

    def test_avg_ms_correct(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.record_request("GET", "/api/test", 200, 0.03)
        m = hm.get_metrics()
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["avg_ms"] == 20.0  # (10+30)/2


# ── reset_metrics ─────────────────────────────────────────────────────

class TestResetMetrics:

    def test_resets_request_count(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.reset_metrics()
        m = hm.get_metrics()
        assert m["request_count"] == 0

    def test_resets_error_count(self):
        hm.record_request("GET", "/api/test", 500, 0.01)
        hm.reset_metrics()
        m = hm.get_metrics()
        assert m["error_count"] == 0

    def test_resets_db_query_count(self):
        hm.record_db_query(success=True)
        hm.reset_metrics()
        m = hm.get_metrics()
        assert m["db_query_count"] == 0

    def test_resets_db_error_count(self):
        hm.record_db_query(success=False)
        hm.reset_metrics()
        m = hm.get_metrics()
        assert m["db_error_count"] == 0

    def test_resets_endpoint_latency(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.reset_metrics()
        m = hm.get_metrics()
        assert m["endpoint_latency"] == {}

    def test_uptime_restarts_after_reset(self):
        time.sleep(0.05)
        hm.reset_metrics()
        time.sleep(0.05)
        m = hm.get_metrics()
        # Uptime should be ~0.05s (from second sleep), not ~0.1s
        assert m["uptime_seconds"] < 0.15

    def test_can_record_after_reset(self):
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.reset_metrics()
        hm.record_request("GET", "/api/test", 200, 0.01)
        m = hm.get_metrics()
        assert m["request_count"] == 1


# ── Integration / edge cases ──────────────────────────────────────────

class TestIntegration:

    def test_full_workflow(self):
        """Simulate a realistic sequence of operations."""
        # Some requests
        hm.record_request("GET", "/api/portfolio", 200, 0.045)
        hm.record_request("GET", "/api/wallets", 200, 0.120)
        hm.record_request("POST", "/api/wallets", 201, 0.200)
        hm.record_request("GET", "/api/health", 500, 0.001)  # error

        # Some DB queries
        hm.record_db_query(success=True)
        hm.record_db_query(success=True)
        hm.record_db_query(success=False)

        m = hm.get_metrics()
        assert m["request_count"] == 4
        assert m["error_count"] == 1
        assert m["error_rate"] == 25.0
        assert m["db_query_count"] == 3
        assert m["db_error_count"] == 1
        assert len(m["endpoint_latency"]) == 4

    def test_zero_division_safety(self):
        """No division by zero when counts are 0."""
        m = hm.get_metrics()
        # These should not raise
        assert m["error_rate"] == 0.0
        assert m["db_error_rate"] == 0.0

    def test_concurrent_endpoints(self):
        """Multiple endpoints tracked independently."""
        endpoints = [
            ("GET", "/api/portfolio"),
            ("GET", "/api/wallets"),
            ("POST", "/api/wallets"),
            ("GET", "/api/signals"),
            ("GET", "/api/alerts"),
        ]
        for method, path in endpoints:
            hm.record_request(method, path, 200, 0.05)
        m = hm.get_metrics()
        assert len(m["endpoint_latency"]) == 5

    def test_metrics_are_snapshot(self):
        """get_metrics returns a snapshot; subsequent changes don't affect it."""
        hm.record_request("GET", "/api/test", 200, 0.01)
        m1 = hm.get_metrics()
        hm.record_request("GET", "/api/test", 200, 0.01)
        m2 = hm.get_metrics()
        assert m1["request_count"] == 1
        assert m2["request_count"] == 2

    def test_large_number_of_requests(self):
        """System handles many requests without error."""
        for _ in range(1000):
            hm.record_request("GET", "/api/test", 200, 0.001)
        m = hm.get_metrics()
        assert m["request_count"] == 1000
        # Ring buffer caps at 100
        stats = m["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 100


# ── /api/health/metrics endpoint integration ───────────────────────────

class TestHealthMetricsEndpoint:
    """
    Test the GET /api/health/metrics endpoint that exposes collected metrics.
    Uses the FastAPI TestClient from the conftest fixtures.
    """

    def test_endpoint_returns_200(self, test_client):
        """The metrics endpoint should return HTTP 200."""
        resp = test_client.get("/api/health/metrics")
        assert resp.status_code == 200

    def test_endpoint_returns_expected_keys(self, test_client):
        """The response should contain all expected metric keys."""
        # Seed some data first
        from services import health_metrics as hm
        hm.reset_metrics()
        hm.record_request("GET", "/api/test", 200, 0.01)

        resp = test_client.get("/api/health/metrics")
        data = resp.json()
        expected_keys = {
            "uptime_seconds", "uptime_human", "request_count", "error_count",
            "error_rate", "db_query_count", "db_error_count", "db_error_rate",
            "endpoint_latency", "monitor_phases",
        }
        assert set(data.keys()) == expected_keys

    def test_endpoint_reflects_recorded_requests(self, test_client):
        """After recording requests, the endpoint should reflect them."""
        from services import health_metrics as hm
        hm.reset_metrics()
        hm.record_request("GET", "/api/test", 200, 0.05)

        resp = test_client.get("/api/health/metrics")
        data = resp.json()
        assert data["request_count"] == 1
        assert data["error_count"] == 0
        assert data["error_rate"] == 0.0

    def test_endpoint_reflects_errors(self, test_client):
        """After recording an error, the endpoint should show it."""
        from services import health_metrics as hm
        hm.reset_metrics()
        hm.record_request("GET", "/api/test", 200, 0.01)
        hm.record_request("GET", "/api/test", 500, 0.01)

        resp = test_client.get("/api/health/metrics")
        data = resp.json()
        assert data["request_count"] == 2
        assert data["error_count"] == 1
        assert data["error_rate"] == 50.0

    def test_endpoint_includes_latency_percentiles(self, test_client):
        """With enough samples, p50/p95/p99 should be present."""
        from services import health_metrics as hm
        hm.reset_metrics()
        for i in range(100):
            hm.record_request("GET", "/api/test", 200, 0.001 * (i + 1))

        resp = test_client.get("/api/health/metrics")
        data = resp.json()
        stats = data["endpoint_latency"]["GET /api/test"]
        assert stats["count"] == 100
        assert stats["p50_ms"] is not None
        assert stats["p95_ms"] is not None
        assert stats["p99_ms"] is not None
        assert stats["avg_ms"] is not None

    def test_endpoint_uptime_is_positive(self, test_client):
        """Uptime should be a positive number."""
        import time
        from services import health_metrics as hm
        hm.reset_metrics()
        time.sleep(0.05)

        resp = test_client.get("/api/health/metrics")
        data = resp.json()
        assert data["uptime_seconds"] >= 0.05
        assert isinstance(data["uptime_human"], str)
