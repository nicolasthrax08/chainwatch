#!/usr/bin/env python3
"""
Tests for the GET /api/health/pool endpoint.
Covers: pool available/unavailable, stats shape, utilization calculation,
error handling, and integration with the FastAPI test client.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only")

from fastapi.testclient import TestClient


# ── Helper: build a mock pool with controllable stats ──────────────────

def _make_mock_pool(size=5, idle=3, min_size=2, max_size=10):
    """Return a MagicMock that mimics asyncpg pool stats methods."""
    pool = MagicMock()
    pool.get_size.return_value = size
    pool.get_idle_size.return_value = idle
    pool.get_min_size.return_value = min_size
    pool.get_max_size.return_value = max_size
    return pool


# ── Unit-level tests (patch main.db_pool directly) ─────────────────────

class TestHealthPoolEndpointUnit:
    """Test the /api/health/pool endpoint by patching main.db_pool."""

    def _get_client_and_main(self):
        """Import main fresh so db_pool starts as None, then return (client, main_mod)."""
        # We must re-import main each time to get a clean module state,
        # but since pytest caches imports, we patch the module-level variable.
        from main import app, db_pool as original_pool
        return app, original_pool

    def test_pool_not_available_returns_available_false(self):
        """When db_pool is None, endpoint returns available=False."""
        import main as main_mod
        main_mod.db_pool = None
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "detail" in data

    def test_pool_available_returns_full_stats(self):
        """When db_pool is healthy, endpoint returns all pool stats."""
        import main as main_mod
        mock_pool = _make_mock_pool(size=5, idle=3, min_size=2, max_size=10)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["size"] == 5
        assert data["idle"] == 3
        assert data["used"] == 2
        assert data["min_size"] == 2
        assert data["max_size"] == 10
        assert data["utilization_pct"] == 20.0  # 2/10 * 100
        assert data["healthy"] is True

    def test_pool_fully_utilized_returns_healthy_false(self):
        """When used == max_size, healthy should be False."""
        import main as main_mod
        mock_pool = _make_mock_pool(size=10, idle=0, min_size=2, max_size=10)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert data["available"] is True
        assert data["used"] == 10
        assert data["utilization_pct"] == 100.0
        assert data["healthy"] is False  # used == max → not healthy

    def test_pool_near_capacity(self):
        """Pool at 80% utilization should still be healthy."""
        import main as main_mod
        mock_pool = _make_mock_pool(size=8, idle=0, min_size=2, max_size=10)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert data["used"] == 8
        assert data["utilization_pct"] == 80.0
        assert data["healthy"] is True  # 8 < 10

    def test_pool_empty_is_healthy(self):
        """Pool with 0 connections is healthy (nothing in use)."""
        import main as main_mod
        mock_pool = _make_mock_pool(size=0, idle=0, min_size=2, max_size=10)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert data["available"] is True
        assert data["size"] == 0
        assert data["used"] == 0
        assert data["utilization_pct"] == 0.0
        assert data["healthy"] is True

    def test_pool_stats_error_returns_error_key(self):
        """If pool methods raise, endpoint returns available=True with error."""
        import main as main_mod
        mock_pool = MagicMock()
        mock_pool.get_size.side_effect = RuntimeError("pool closed")
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert "error" in data
        assert "pool closed" in data["error"]

    def test_response_shape_when_available(self):
        """Response should contain exactly the expected keys when available."""
        import main as main_mod
        mock_pool = _make_mock_pool()
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        expected_keys = {
            "available", "size", "idle", "used",
            "min_size", "max_size", "utilization_pct", "healthy",
        }
        assert set(data.keys()) == expected_keys

    def test_response_shape_when_unavailable(self):
        """Response should contain only available and detail when unavailable."""
        import main as main_mod
        main_mod.db_pool = None
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert set(data.keys()) == {"available", "detail"}

    def test_utilization_rounds_to_one_decimal(self):
        """Utilization should be rounded to 1 decimal place."""
        import main as main_mod
        # 3/7 = 42.857...% → should round to 42.9
        mock_pool = _make_mock_pool(size=3, idle=0, min_size=1, max_size=7)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert data["utilization_pct"] == 42.9

    def test_pool_with_one_max_size(self):
        """Edge case: max_size=1 should not cause division issues."""
        import main as main_mod
        mock_pool = _make_mock_pool(size=1, idle=0, min_size=1, max_size=1)
        main_mod.db_pool = mock_pool
        client = TestClient(main_mod.app)
        resp = client.get("/api/health/pool")
        data = resp.json()
        assert data["utilization_pct"] == 100.0
        assert data["healthy"] is False  # used (1) == max (1)

    def test_no_auth_required(self):
        """The pool endpoint should be publicly accessible (no auth header)."""
        import main as main_mod
        main_mod.db_pool = None
        client = TestClient(main_mod.app)
        # No Authorization header
        resp = client.get("/api/health/pool")
        assert resp.status_code == 200
        # Should NOT return 401 or 403
        assert resp.status_code not in (401, 403)
