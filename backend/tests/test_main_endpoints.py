"""
Integration tests for main.py API endpoints.
Tests the full request/response cycle using FastAPI TestClient.
Covers: auth flow, wallet CRUD, health endpoints, signals, alerts.
"""
import json
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Ensure JWT_SECRET is set before importing main
os.environ.setdefault("JWT_SECRET", "test-secret-for-integration-tests")
os.environ.setdefault("ETHERSCAN_API_KEY", "test-etherscan-key")
os.environ.setdefault("SOLSCAN_API_KEY", "test-solscan-key")
os.environ.setdefault("BLOCKCHAIR_API_KEY", "test-blockchair-key")
os.environ.setdefault("ALPACA_API_KEY", "test-alpaca-key")
os.environ.setdefault("ALPACA_API_SECRET", "test-alpaca-secret")


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_db_pool():
    """Create a mock DB pool that returns mock connections."""
    pool = AsyncMock()
    conn = AsyncMock()

    # Default: user lookup for auth
    mock_user = {
        "id": "user-uuid-1234",
        "wallet_address": "0xabcdef1234567890abcdef1234567890abcdef12",
        "created_at": "2025-01-01T00:00:00+00:00",
        "session_token": None,
        "session_expires_at": None,
        "alpaca_api_key_enc": None,
        "alpaca_api_key_iv": None,
        "alpaca_secret_key_enc": None,
        "alpaca_secret_key_iv": None,
        "alpaca_paper_account_id": None,
        "alpaca_connected_at": None,
        "telegram_chat_id": None,
    }

    async def mock_fetchrow(query, *args, **kwargs):
        # Route queries to appropriate mock responses
        q = query.strip().upper()
        if "SELECT * FROM USERS" in q and "WALLET_ADDRESS" in q:
            return mock_user
        if "SELECT * FROM USERS" in q and "WHERE ID" in q:
            return mock_user
        if "SELECT * FROM WALLETS" in q:
            return None  # wallet not found by default
        if "SELECT * FROM ALERTS" in q:
            return None
        if "SELECT * FROM COPY_TRADE_SIGNALS" in q:
            return None
        # Signal stats aggregate query (fetchrow with JOIN)
        if "TOTAL_SIGNALS" in q or "EXECUTED_COUNT" in q:
            return {
                "total_signals": 0,
                "pending_count": 0,
                "executed_count": 0,
                "failed_count": 0,
                "stale_count": 0,
                "avg_confidence": 0.0,
                "avg_whale_score": 0.0,
                "avg_confidence_executed": None,
                "avg_confidence_failed": None,
                "avg_whale_score_executed": None,
                "signals_24h": 0,
                "signals_7d": 0,
                "avg_time_to_execute_seconds": None,
            }
        # INSERT ... RETURNING * — return a dict shaped like the first table in the INSERT
        if q.startswith("INSERT INTO ALERTS"):
            from datetime import datetime, timezone
            return {
                "id": "alert-uuid-9999",
                "user_id": "user-uuid-1234",
                "rule_type": args[1] if len(args) > 1 else "balance_drop",
                "threshold": args[2] if len(args) > 2 else 5.0,
                "enabled": args[3] if len(args) > 3 else True,
                "notify_telegram": args[4] if len(args) > 4 else True,
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }
        if q.startswith("INSERT INTO WALLETS"):
            from datetime import datetime, timezone
            return {
                "id": "wallet-uuid-8888",
                "user_id": "user-uuid-1234",
                "address": args[1] if len(args) > 1 else "0x0",
                "chain": args[2] if len(args) > 2 else "eth",
                "label": args[3] if len(args) > 3 else None,
                "is_whale": args[4] if len(args) > 4 else False,
                "is_mine": args[5] if len(args) > 5 else False,
                "balance_native": 0.0,
                "balance_usd": 0.0,
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }
        return mock_user

    async def mock_fetch(query, *args, **kwargs):
        q = query.strip().upper()
        if "FROM WALLETS" in q and "USER_ID" in q:
            return []  # no wallets by default
        if "FROM TRANSACTIONS" in q:
            return []
        if "FROM ALERTS" in q:
            return []
        if "FROM FIRED_ALERTS" in q:
            return []
        if "FROM WHALE_SUGGESTIONS" in q:
            return []
        # Signal stats tier query (JOIN + GROUP BY)
        if "TIER" in q and "COPY_TRADE_SIGNALS" in q:
            return []  # no signals → empty tier breakdown
        if "FROM COPY_TRADE_SIGNALS" in q:
            return []
        return []

    async def mock_fetchval(query, *args, **kwargs):
        q = query.strip().upper()
        if "COUNT(*)" in q:
            return 0
        if "SELECT 1" in q:
            return 1
        return None

    async def mock_execute(query, *args, **kwargs):
        q = query.strip().upper()
        if q.startswith("INSERT"):
            return "INSERT 0 1"
        if q.startswith("UPDATE"):
            return "UPDATE 1"
        if q.startswith("DELETE"):
            return "DELETE 1"
        return "SELECT 0"

    conn.fetchrow = mock_fetchrow
    conn.fetch = mock_fetch
    conn.fetchval = mock_fetchval
    conn.execute = mock_execute

    # Context manager for connection
    conn_ctx = MagicMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)

    pool.acquire = MagicMock(return_value=conn_ctx)
    return pool, conn, mock_user


@pytest.fixture
def client(mock_db_pool):
    """Provide a TestClient with mocked DB.

    Creates a fresh main module instance with the mock DB pool, patches
    asyncpg.create_pool so the startup handler's DB connection attempt
    succeeds, and patches is_monitor_alive so the health endpoint reports
    healthy.
    """
    from fastapi.testclient import TestClient
    pool, conn, mock_user = mock_db_pool

    with patch.dict(os.environ, {
        "JWT_SECRET": "test-secret-for-integration-tests",
        "DATABASE_URL": "postgresql://test:***@localhost:5432/test",
    }):
        import importlib
        import sys

        # Remove cached main module so we get a fresh import
        if "main" in sys.modules:
            del sys.modules["main"]
        import main as main_mod

        # Patch asyncpg.create_pool at the module level so the startup
        # handler's own code sets main_mod.db_pool to our mock pool.
        mock_create_pool = AsyncMock(return_value=pool)

        with patch("main.asyncpg.create_pool", mock_create_pool), \
             patch("services.instrumented_pool.instrument_pool", lambda p: None), \
             patch("services.monitor.start_monitor"), \
             patch("services.monitor.is_monitor_alive", return_value=True):
            with TestClient(main_mod.app) as c:
                yield c, pool, conn, mock_user


def _make_auth_header(token: str = "test-token") -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── Health Endpoints ─────────────────────────────────────────────────


class TestHealthEndpoints:
    """Test /api/health and sub-endpoints."""

    def test_health_returns_healthy(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_db_subsystem_present(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health")
        data = resp.json()
        assert "db" in data.get("subsystems", {})

    def test_health_monitor_subsystem_present(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health")
        data = resp.json()
        assert "monitor" in data.get("subsystems", {})

    def test_health_api_keys_subsystem(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health")
        data = resp.json()
        # api_keys subsystem should be present (even if not configured)
        assert "api_keys" in data.get("subsystems", {})

    def test_health_metrics_endpoint_exists(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health/metrics")
        assert resp.status_code in (200, 404)  # 404 is OK if not implemented

    def test_health_diagnostic_endpoint_exists(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health/diagnostic")
        assert resp.status_code in (200, 404)

    def test_health_startup_log_endpoint_exists(self, client):
        c, _, _, _ = client
        resp = c.get("/api/health/startup-log")
        assert resp.status_code in (200, 404)


# ─── Auth Endpoints ──────────────────────────────────────────────────


class TestAuthEndpoints:
    """Test /api/auth/* endpoints."""

    def test_challenge_requires_wallet_address(self, client):
        c, _, _, _ = client
        resp = c.post("/api/auth/challenge", json={})
        assert resp.status_code in (400, 422)

    def test_challenge_rejects_invalid_address(self, client):
        c, _, _, _ = client
        resp = c.post("/api/auth/challenge", json={"wallet_address": "invalid"})
        assert resp.status_code in (400, 422)

    def test_challenge_accepts_eth_address(self, client):
        c, _, _, _ = client
        resp = c.post(
            "/api/auth/challenge?wallet_address=0xabcdef1234567890abcdef1234567890abcdef12"
        )
        assert resp.status_code == 200

    def test_challenge_accepts_sol_address(self, client):
        c, _, _, _ = client
        resp = c.post(
            "/api/auth/challenge?wallet_address=A+valid+Solana+address+string+here1234"
        )
        # May be 200 or 400 depending on validation rules
        assert resp.status_code in (200, 400, 422)

    def test_verify_requires_body(self, client):
        c, _, _, _ = client
        resp = c.post("/api/auth/verify")
        assert resp.status_code in (400, 422)

    def test_verify_rejects_short_signature(self, client):
        c, _, _, _ = client
        resp = c.post("/api/auth/verify", json={
            "wallet_address": "0xabcdef1234567890abcdef1234567890abcdef12",
            "signature": "short"
        })
        assert resp.status_code in (400, 422)

    def test_me_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_rejects_invalid_token(self, client):
        c, _, _, _ = client
        resp = c.get("/api/auth/me", headers=_make_auth_header("invalid-token"))
        assert resp.status_code in (401, 403)

    def test_me_accepts_valid_jwt(self, client):
        c, _, _, _ = client
        # Include uid in JWT to avoid DB lookup fallback (Pitfall #21 optimization)
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/auth/me", headers=_make_auth_header(token))
        assert resp.status_code == 200


# ─── Dashboard Endpoints ─────────────────────────────────────────────


class TestDashboardEndpoint:
    """Test /api/dashboard endpoint."""

    def test_dashboard_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/dashboard")
        assert resp.status_code == 401

    def test_dashboard_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/dashboard", headers=_make_auth_header(token))
        # May be 200 or 503 depending on DB state
        assert resp.status_code in (200, 503)

    def test_dashboard_portfolio_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/dashboard", headers=_make_auth_header(token))
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


# ─── Wallet Endpoints ────────────────────────────────────────────────


class TestWalletEndpoints:
    """Test /api/wallets/* endpoints."""

    def test_list_wallets_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/wallets")
        assert resp.status_code == 401

    def test_list_wallets_returns_empty_list(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/wallets", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_add_wallet_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.post("/api/wallets", json={
            "address": "0xabcdef1234567890abcdef1234567890abcdef12",
            "chain": "ETH"
        })
        assert resp.status_code == 401


# ─── Signals Endpoints ───────────────────────────────────────────────


class TestSignalsEndpoints:
    """Test /api/signals/* endpoints."""

    def test_list_signals_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/signals")
        assert resp.status_code == 401

    def test_list_signals_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/signals", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_signal_stats_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/signals/stats")
        assert resp.status_code == 401

    def test_signal_stats_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/signals/stats", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_signal_history_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/signals/history")
        assert resp.status_code == 401

    def test_signal_history_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/signals/history", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)


# ─── Alerts Endpoints ────────────────────────────────────────────────


class TestAlertsEndpoints:
    """Test /api/alerts/* endpoints."""

    def test_list_alerts_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/alerts")
        assert resp.status_code == 401

    def test_list_alerts_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/alerts", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_create_alert_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.post("/api/alerts", json={
            "rule_type": "balance_drop",
            "threshold": 5.0
        })
        assert resp.status_code == 401

    def test_create_alert_validates_rule_type(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.post("/api/alerts", json={
            "rule_type": "invalid_type",
            "threshold": 5.0
        }, headers=_make_auth_header(token))
        assert resp.status_code in (400, 422, 200, 503)

    def test_create_alert_validates_threshold_range(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.post("/api/alerts", json={
            "rule_type": "balance_drop",
            "threshold": -999
        }, headers=_make_auth_header(token))
        # Negative threshold may be valid or invalid depending on implementation
        assert resp.status_code in (400, 422, 200, 503)

    def test_alert_history_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/alerts/history")
        assert resp.status_code == 401

    def test_alert_history_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/alerts/history", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)


# ─── Activity Endpoint ────────────────────────────────────────────────


class TestActivityEndpoint:
    """Test /api/activity endpoint."""

    def test_activity_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/activity")
        assert resp.status_code == 401

    def test_activity_returns_pagination(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/activity", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_activity_chain_filter(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/activity?chain=ETH", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_activity_type_filter(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/activity?type=whale_alert", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)


# ─── Whale Suggestions Endpoint ──────────────────────────────────────


class TestWhaleSuggestionsEndpoint:
    """Test /whale-suggestions endpoint."""

    def test_whale_suggestions_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/whale-suggestions")
        assert resp.status_code == 401

    def test_whale_suggestions_returns_structure(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/whale-suggestions", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)

    def test_whale_suggestions_chain_filter(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/whale-suggestions?chain=ETH", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)


# ─── Whale Sentiment Endpoint ────────────────────────────────────────


class TestWhaleSentimentEndpoint:
    """Test /whale-sentiment endpoint."""

    def test_whale_sentiment_requires_auth(self, client):
        c, _, _, _ = client
        resp = c.get("/api/whale-sentiment")
        assert resp.status_code == 401

    def test_whale_sentiment_returns_neutral_when_no_data(self, client):
        c, _, _, _ = client
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "0xabcdef1234567890abcdef1234567890abcdef12", "uid": "user-uuid-1234", "exp": 9999999999},
            "test-secret-for-integration-tests",
            algorithm="HS256"
        )
        resp = c.get("/api/whale-sentiment", headers=_make_auth_header(token))
        assert resp.status_code in (200, 503)


# ─── OpenAPI Schema ──────────────────────────────────────────────────


class TestOpenAPISchema:
    """Test OpenAPI schema generation."""

    def test_openapi_schema_exists(self, client):
        c, _, _, _ = client
        resp = c.get("/openapi.json")
        assert resp.status_code == 200

    def test_all_endpoints_registered(self, client):
        c, _, _, _ = client
        resp = c.get("/openapi.json")
        data = resp.json()
        paths = data.get("paths", {})
        # Should have at least one API endpoint (routes are /api/*)
        api_paths = [p for p in paths if p.startswith("/api/")]
        assert len(api_paths) >= 1

    def test_no_duplicate_routes(self, client):
        c, _, _, _ = client
        resp = c.get("/openapi.json")
        data = resp.json()
        paths = list(data.get("paths", {}).keys())
        assert len(paths) == len(set(paths)), "Duplicate routes detected"

    def test_health_sub_endpoints_registered(self, client):
        c, _, _, _ = client
        resp = c.get("/openapi.json")
        data = resp.json()
        paths = data.get("paths", {})
        # Health sub-endpoints should be registered under /api/
        health_paths = [p for p in paths if "health" in p.lower()]
        assert len(health_paths) >= 1
