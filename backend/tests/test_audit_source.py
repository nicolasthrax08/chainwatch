#!/usr/bin/env python3
"""
Tests for the audit_source.py static analysis tool.
Verifies that each check function correctly detects (or passes) known patterns.
"""
import os
import sys
import tempfile
import textwrap

import pytest

# Add parent dir so we can import audit_source
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_source as aud


# ─── Helpers ────────────────────────────────────────────────────────────

def _make_py_files(tmpdir, files: dict) -> list:
    """Create Python files in tmpdir. Returns list of full paths."""
    paths = []
    for name, content in files.items():
        fpath = os.path.join(tmpdir, name)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(textwrap.dedent(content))
        paths.append(fpath)
    return paths


def _make_sql_files(tmpdir, files: dict) -> list:
    paths = []
    for name, content in files.items():
        fpath = os.path.join(tmpdir, name)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(textwrap.dedent(content))
        paths.append(fpath)
    return paths


# ─── check_start_monitor_wired ──────────────────────────────────────────

class TestCheckStartMonitorWired:

    def test_passes_when_start_monitor_in_startup_handler(self, tmp_path):
        """start_monitor called inside @app.on_event('startup') → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                @app.on_event("startup")
                async def startup():
                    start_monitor(pool)
            """
        })
        result = aud.AuditResult()
        aud.check_start_monitor_wired(py_files, result)
        assert len(result.critical) == 0
        assert any("Pitfall #26" in p for p in result.passed)

    def test_critical_when_start_monitor_called_without_startup(self, tmp_path):
        """start_monitor called but NOT in startup handler → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                from services.monitor import start_monitor

                async def some_helper():
                    start_monitor(pool)
            """
        })
        result = aud.AuditResult()
        aud.check_start_monitor_wired(py_files, result)
        assert len(result.critical) >= 1
        assert any("#26" in f.pitfall for f in result.critical)

    def test_skips_test_files(self, tmp_path):
        """Test files that call start_monitor should NOT trigger critical."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        py_files = _make_py_files(tests_dir, {
            "test_monitor.py": """
                async def test_start_monitor():
                    start_monitor(pool)
            """
        })
        result = aud.AuditResult()
        aud.check_start_monitor_wired(py_files, result)
        assert len(result.critical) == 0

    def test_passes_when_start_monitor_only_defined_not_called(self, tmp_path):
        """File that defines start_monitor but doesn't call it → pass."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                def start_monitor(pool):
                    pass
            """
        })
        result = aud.AuditResult()
        aud.check_start_monitor_wired(py_files, result)
        assert len(result.critical) == 0
        assert any("defines but does not call" in p for p in result.passed)


# ─── check_on_conflict_has_constraint ───────────────────────────────────

class TestCheckOnConflictHasConstraint:

    def test_passes_when_constraint_exists(self, tmp_path):
        """ON CONFLICT with matching unique constraint → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                await conn.execute(
                    "INSERT INTO wallets (id, address) VALUES ($1, $2)"
                    " ON CONFLICT (id) DO NOTHING"
                )
            """
        })
        sql_files = _make_sql_files(tmp_path, {
            "001_initial.sql": """
                CREATE TABLE wallets (
                    id UUID PRIMARY KEY,
                    address TEXT UNIQUE
                );
            """
        })
        result = aud.AuditResult()
        aud.check_on_conflict_has_constraint(py_files, sql_files, result)
        assert len(result.critical) == 0

    def test_critical_when_no_matching_constraint(self, tmp_path):
        """ON CONFLICT without matching unique constraint → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                await conn.execute(
                    "INSERT INTO wallets (id, address, chain) VALUES ($1, $2, $3)"
                    " ON CONFLICT (id, address, chain) DO NOTHING"
                )
            """
        })
        sql_files = _make_sql_files(tmp_path, {
            "001_initial.sql": """
                CREATE TABLE wallets (
                    id UUID PRIMARY KEY,
                    address TEXT,
                    chain TEXT
                );
            """
        })
        result = aud.AuditResult()
        aud.check_on_conflict_has_constraint(py_files, sql_files, result)
        assert len(result.critical) >= 1
        assert any("#19" in f.pitfall for f in result.critical)

    def test_passes_when_no_on_conflict(self, tmp_path):
        """No ON CONFLICT usage → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                await conn.execute("SELECT 1")
            """
        })
        sql_files = _make_sql_files(tmp_path, {
            "001_initial.sql": "CREATE TABLE wallets (id UUID PRIMARY KEY);"
        })
        result = aud.AuditResult()
        aud.check_on_conflict_has_constraint(py_files, sql_files, result)
        assert len(result.critical) == 0
        assert any("No ON CONFLICT" in p for p in result.passed)


# ─── check_columns_exist_in_schema ──────────────────────────────────────

class TestCheckColumnsExistInSchema:

    def test_passes_when_all_columns_exist(self, tmp_path):
        """All referenced columns exist in schema → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                await conn.execute(
                    "UPDATE wallets SET balance_usd = $1 WHERE id = $2"
                )
            """
        })
        sql_files = _make_sql_files(tmp_path, {
            "001_initial.sql": """
                CREATE TABLE wallets (
                    id UUID PRIMARY KEY,
                    balance_usd DECIMAL(20,2)
                );
            """
        })
        result = aud.AuditResult()
        aud.check_columns_exist_in_schema(py_files, sql_files, result)
        assert len(result.critical) == 0

    def test_critical_when_column_missing(self, tmp_path):
        """Referenced column not in schema → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                await conn.execute(
                    "UPDATE wallets SET nonexistent_col = $1 WHERE id = $2"
                )
            """
        })
        sql_files = _make_sql_files(tmp_path, {
            "001_initial.sql": """
                CREATE TABLE wallets (
                    id UUID PRIMARY KEY,
                    balance_usd DECIMAL(20,2)
                );
            """
        })
        result = aud.AuditResult()
        aud.check_columns_exist_in_schema(py_files, sql_files, result)
        all_findings = result.critical + result.minor
        assert len(all_findings) >= 1


# ─── check_ws_auth_before_accept ────────────────────────────────────────

class TestCheckWsAuthBeforeAccept:

    def test_passes_when_auth_before_accept(self, tmp_path):
        """JWT verified before websocket.accept() → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                @app.websocket("/ws")
                async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
                    payload = verify_jwt(token)
                    await websocket.accept()
            """
        })
        result = aud.AuditResult()
        aud.check_ws_auth_before_accept(py_files, result)
        assert len(result.critical) == 0

    def test_critical_when_accept_before_auth(self, tmp_path):
        """accept() before JWT verification → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                @app.websocket("/ws")
                async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
                    await websocket.accept()
                    payload = verify_jwt(token)
            """
        })
        result = aud.AuditResult()
        aud.check_ws_auth_before_accept(py_files, result)
        assert len(result.critical) >= 1


# ─── check_cache_initialized_nonzero ────────────────────────────────────

class TestCheckCacheInitializedNonzero:

    def test_passes_when_cache_initialized_with_nonzero(self, tmp_path):
        """Cache initialized with non-zero default → pass."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                _price_cache = {
                    "ETH": 2500.0,  # price
                    "SOL": 170.0,  # price
                    "BTC": 105000.0,  # price
                }
            """
        })
        result = aud.AuditResult()
        aud.check_cache_initialized_nonzero(py_files, result)
        assert len(result.critical) == 0
        assert len(result.minor) == 0

    def test_minor_when_cache_initialized_to_zero_with_price_comment(self, tmp_path):
        """Cache initialized to 0.0 with price/rate comment → minor."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                _price_cache = {
                    "ETH": 0.0,  # price
                    "SOL": 0.0,  # rate
                }
            """
        })
        result = aud.AuditResult()
        aud.check_cache_initialized_nonzero(py_files, result)
        assert len(result.minor) >= 1


# ─── check_get_current_user_no_db ───────────────────────────────────────

class TestCheckGetCurrentUserNoDB:

    def test_passes_when_uid_in_jwt(self, tmp_path):
        """get_current_user extracts uid from JWT → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                async def get_current_user(authorization: str = Header(...)):
                    payload = verify_jwt(token)
                    user_id = payload.get("uid")
                    if user_id:
                        return {"id": user_id}
            """
        })
        result = aud.AuditResult()
        aud.check_get_current_user_no_db(py_files, result)
        assert len(result.critical) == 0

    def test_critical_when_db_lookup_every_request(self, tmp_path):
        """get_current_user does DB lookup without checking JWT uid → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                async def get_current_user(authorization: str = Header(...)):
                    payload = verify_jwt(token)
                    async with acquire_db() as conn:
                        user = await conn.fetchrow(
                            "SELECT * FROM users WHERE wallet_address = $1",
                            payload["sub"]
                        )
                    return dict(user)
            """
        })
        result = aud.AuditResult()
        aud.check_get_current_user_no_db(py_files, result)
        assert len(result.critical) >= 1


# ─── check_db_conn_held_across_http ─────────────────────────────────────

class TestCheckDbConnHeldAcrossHTTP:

    def test_passes_when_conn_released_before_http(self, tmp_path):
        """DB connection released before HTTP call → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                async def refresh_wallet(wallet_id):
                    async with acquire_db() as conn:
                        wallet = await conn.fetchrow("SELECT * FROM wallets WHERE id = $1", wallet_id)
                    resp = await httpx.get("https://api.example.com/price")
            """
        })
        result = aud.AuditResult()
        aud.check_db_conn_held_across_http(py_files, result)
        assert len(result.critical) == 0

    def test_critical_when_conn_held_during_http(self, tmp_path):
        """DB connection held during HTTP call → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                async def refresh_wallet(wallet_id):
                    async with acquire_db() as conn:
                        wallet = await conn.fetchrow("SELECT * FROM wallets WHERE id = $1", wallet_id)
                        resp = await httpx.get("https://api.example.com/price")
            """
        })
        result = aud.AuditResult()
        aud.check_db_conn_held_across_http(py_files, result)
        assert len(result.critical) >= 1


# ─── check_cron_secret_fail_closed ──────────────────────────────────────

class TestCheckCronSecretFailClosed:

    def test_passes_when_fail_closed(self, tmp_path):
        """CRON_SECRET check denies access when not configured → pass."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                _CRON_SECRET = os.environ.get("CRON_SECRET", "")

                async def _require_cron_secret(authorization: str = Header("")):
                    if not _CRON_SECRET:
                        raise HTTPException(status_code=503)
                    if authorization != f"Bearer {_CRON_SECRET}":
                        raise HTTPException(status_code=403)
            """
        })
        result = aud.AuditResult()
        aud.check_cron_secret_fail_closed(py_files, result)
        assert len(result.critical) == 0

    def test_critical_when_fail_open(self, tmp_path):
        """CRON_SECRET auth uses `if _CRON_SECRET and auth !=` (fail-open) → critical."""
        py_files = _make_py_files(tmp_path, {
            "main.py": """
                _CRON_SECRET = os.environ.get("CRON_SECRET", "")

                async def _require_cron_secret(authorization: str = Header("")):
                    if _CRON_SECRET and authorization != f"Bearer {_CRON_SECRET}":
                        raise HTTPException(status_code=403)
            """
        })
        result = aud.AuditResult()
        aud.check_cron_secret_fail_closed(py_files, result)
        assert len(result.critical) >= 1


# ─── check_unbounded_state_dicts ────────────────────────────────────────

class TestCheckUnboundedStateDicts:

    def test_passes_when_prune_present(self, tmp_path):
        """Module-level state dict with prune function → pass."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                _cooldown_cache: dict = {}

                def _prune_cooldown_cache() -> None:
                    import time
                    now = time.time()
                    expired = [k for k, v in _cooldown_cache.items() if now - v > 600]
                    for k in expired:
                        del _cooldown_cache[k]

                def _mark_cooldown(alert_id: str) -> None:
                    import time
                    _cooldown_cache[alert_id] = time.time()
            """
        })
        result = aud.AuditResult()
        aud.check_unbounded_state_dicts(py_files, result)
        assert len(result.critical) == 0

    def test_critical_when_no_prune(self, tmp_path):
        """Module-level state dict without prune → critical."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                _cooldown_cache: dict = {}

                def _mark_cooldown(alert_id: str) -> None:
                    import time
                    _cooldown_cache[alert_id] = time.time()
            """
        })
        result = aud.AuditResult()
        aud.check_unbounded_state_dicts(py_files, result)
        assert len(result.critical) >= 1


# ─── check_balance_vs_event_amount ──────────────────────────────────────

class TestCheckBalanceVsEventAmount:

    def test_passes_when_tx_amount_separate(self, tmp_path):
        """Check function returns tx_amount_native separately from balance_native → pass."""
        py_files = _make_py_files(tmp_path, {
            "services/monitor.py": """
                async def _check_wallet_balance_and_txs(wallet_row):
                    return (
                        new_balance_native,
                        new_balance_usd,
                        latest_tx_hash,
                        tx_type,
                        token,
                        tx_amount_native,
                    )
            """
        })
        result = aud.AuditResult()
        aud.check_balance_vs_event_amount(py_files, result)
        assert len(result.critical) == 0


# ─── check_n_plus_one_patterns ──────────────────────────────────────────

class TestCheckNPlusOnePatterns:

    def test_passes_when_batch_query(self, tmp_path):
        """Batch query with WHERE id = ANY($1) → pass."""
        py_files = _make_py_files(tmp_path, {
            "services/alert_evaluator.py": """
                prev_rows = await conn.fetch(
                    "SELECT id, balance_usd FROM wallets WHERE id = ANY($1)",
                    changed_wids,
                )
            """
        })
        result = aud.AuditResult()
        aud.check_n_plus_one_patterns(py_files, result)
        assert len(result.critical) == 0


# ─── Integration: full audit run on actual codebase ─────────────────────

class TestIntegrationFullAudit:

    def test_full_audit_on_real_codebase(self):
        """Run the full audit on the actual backend codebase — should pass clean."""
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        py_files = aud.find_py_files(base)
        sql_files = aud.find_sql_files(base)
        jsx_files = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {
                "node_modules", ".git", "__pycache__", ".venv", "venv", "build", "dist"
            }]
            for f in files:
                if f.endswith((".jsx", ".js")):
                    jsx_files.append(os.path.join(root, f))

        result = aud.AuditResult()
        # Run a representative subset of checks
        aud.check_start_monitor_wired(py_files, result)
        aud.check_on_conflict_has_constraint(py_files, sql_files, result)
        aud.check_columns_exist_in_schema(py_files, sql_files, result)
        aud.check_ws_auth_before_accept(py_files, result)
        aud.check_cache_initialized_nonzero(py_files, result)
        aud.check_get_current_user_no_db(py_files, result)
        aud.check_db_conn_held_across_http(py_files, result)
        aud.check_cron_secret_fail_closed(py_files, result)
        aud.check_unbounded_state_dicts(py_files, result)
        aud.check_balance_vs_event_amount(py_files, result)
        aud.check_n_plus_one_patterns(py_files, result)

        # The real codebase should have zero critical findings
        assert len(result.critical) == 0, (
            f"Critical findings in real codebase: "
            + "; ".join(f"{f.pitfall}: {f.description}" for f in result.critical)
        )
