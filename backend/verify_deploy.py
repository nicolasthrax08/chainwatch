#!/usr/bin/env python3
"""
ChainWatch Deployment Verification Script
==========================================
Verifies that the deployed ChainWatch instance is healthy and matches
the source code's expected behavior. Runs after redeployment to confirm
all past audit findings are resolved.

Usage:
    python verify_deploy.py [--host HOST] [--timeout SECONDS]

Exit codes:
    0 = all checks passed
    1 = critical check failed (blocks production)
    2 = minor check failed (non-blocking)

Checks implemented:
    1. Health endpoint returns PostgreSQL (not in_memory)
    2. All source-defined API endpoints are registered
    3. Unauthenticated endpoints return 401
    4. Whale suggestions require auth (fixes AUTH BYPASS finding)
    5. Challenge endpoint validates address format
    6. Monitor worker is running
    7. Database unique constraints exist (pitfall #19)
    8. All migrations applied (schema column parity — pitfall #20)
"""
import argparse
import asyncio
import json
import sys
import re
from typing import List, Tuple

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

try:
    import asyncpg
except ImportError:
    asyncpg = None  # DB checks will be skipped


# ─── Source-defined endpoints (extracted from backend/main.py OpenAPI spec) ───
EXPECTED_ENDPOINTS = {
    ("GET", "/api/auth/challenge"),
    ("POST", "/api/auth/challenge"),
    ("POST", "/api/auth/verify"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/dashboard/"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/wallets"),
    ("POST", "/api/wallets"),
    ("GET", "/api/whale-suggestions"),
    ("GET", "/api/activity"),
    ("GET", "/api/alerts"),
    ("POST", "/api/alerts"),
    ("GET", "/api/signals"),
    ("GET", "/api/health"),
}

# Endpoints that should require authentication (return 401 without Bearer token)
AUTH_REQUIRED_ENDPOINTS = [
    ("GET", "/api/wallets"),
    ("GET", "/api/whale-suggestions"),
    ("GET", "/api/alerts"),
    ("GET", "/api/signals"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/activity"),
]

# Columns that MUST exist in the schema (from migrations 001-007)
REQUIRED_COLUMNS = {
    "wallets": [
        "balance_native", "balance_usd", "last_balance_update",
        "whale_score", "score_activity", "score_reliability", "score_weight",
        "score_recency", "score_diversity", "score_signals_used",
        "score_calculated_at", "score_is_coldstart",
        "median_amount_30d", "execution_rate_30d",
    ],
    "copy_trade_signals": [
        "explanation", "explanation_stale", "score_at_generation",
    ],
    "alerts": [
        "last_fired_at", "cooldown_seconds",
    ],
    "users": [
        "alpaca_api_key_enc", "alpaca_api_key_iv",
        "alpaca_secret_key_enc", "alpaca_secret_key_iv",
        "alpaca_paper_account_id", "alpaca_connected_at",
    ],
}

# Required unique constraints (pitfall #19)
REQUIRED_UNIQUE_CONSTRAINTS = [
    ("uq_transactions_tx_hash_chain", "transactions"),
    ("uq_users_wallet_address", "users"),
    ("uq_whale_suggestions_chain_address", "whale_suggestions"),
]


class CheckResult:
    def __init__(self, name: str, severity: str, passed: bool, detail: str):
        self.name = name
        self.severity = severity  # "critical" or "minor"
        self.passed = passed
        self.detail = detail

    def __repr__(self):
        icon = "✅" if self.passed else "❌"
        return f"{icon} [{self.severity.upper()}] {self.name}: {self.detail}"


async def run_checks(host: str, timeout: float, db_url: str | None = None) -> List[CheckResult]:
    results = []
    base = host.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        # ── Check 1: Health endpoint ─────────────────────────────────
        try:
            r = await client.get(f"{base}/api/health")
            data = r.json()
            # Support both old format ({"database": "connected"}) and
            # new subsystem format ({"subsystems": {"db": {"ok": true}}})
            db_status = data.get("database")
            if db_status is None:
                # New subsystem format
                db_ok = data.get("subsystems", {}).get("db", {}).get("ok")
                if db_ok is True:
                    results.append(CheckResult(
                        "DB is PostgreSQL", "critical", True,
                        "subsystems.db.ok=true"
                    ))
                elif db_ok is False:
                    results.append(CheckResult(
                        "DB is PostgreSQL", "critical", False,
                        "subsystems.db.ok=false — DB unreachable"
                    ))
                else:
                    results.append(CheckResult(
                        "DB is PostgreSQL", "critical", False,
                        f"health endpoint missing db status: {data}"
                    ))
            elif db_status == "in_memory":
                results.append(CheckResult(
                    "DB is PostgreSQL", "critical", False,
                    f"database={db_status} — all data lost on restart"
                ))
            elif db_status in ("connected", "ok", "postgresql"):
                results.append(CheckResult(
                    "DB is PostgreSQL", "critical", True,
                    f"database={db_status}"
                ))
            else:
                results.append(CheckResult(
                    "DB is PostgreSQL", "critical", False,
                    f"unexpected database status: {db_status}"
                ))
        except Exception as e:
            results.append(CheckResult(
                "DB is PostgreSQL", "critical", False,
                f"health endpoint error: {e}"
            ))

        # ── Check 2: All expected endpoints registered ──────────────
        try:
            r = await client.get(f"{base}/openapi.json")
            spec = r.json()
            live_paths = set()
            for path, methods in spec.get("paths", {}).items():
                for method in methods:
                    live_paths.add((method.upper(), path))

            missing = EXPECTED_ENDPOINTS - live_paths
            extra = live_paths - EXPECTED_ENDPOINTS
            if missing:
                results.append(CheckResult(
                    "Endpoint parity", "critical", False,
                    f"Missing {len(missing)} endpoints: {sorted(missing)[:5]}"
                ))
            else:
                results.append(CheckResult(
                    "Endpoint parity", "critical", True,
                    f"All {len(EXPECTED_ENDPOINTS)} expected endpoints present"
                    + (f" (+{len(extra)} extra)" if extra else "")
                ))
        except Exception as e:
            results.append(CheckResult(
                "Endpoint parity", "critical", False,
                f"OpenAPI fetch error: {e}"
            ))

        # ── Check 3: Auth-required endpoints return 401 ─────────────
        auth_failures = []
        for method, path in AUTH_REQUIRED_ENDPOINTS:
            try:
                if method == "GET":
                    r = await client.get(f"{base}{path}")
                else:
                    r = await client.post(f"{base}{path}", json={})
                if r.status_code != 401:
                    auth_failures.append((method, path, r.status_code))
            except Exception as e:
                auth_failures.append((method, path, str(e)))

        if auth_failures:
            results.append(CheckResult(
                "Auth enforcement", "critical", False,
                f"{len(auth_failures)} endpoints accept unauthenticated requests: "
                f"{auth_failures[:3]}"
            ))
        else:
            results.append(CheckResult(
                "Auth enforcement", "critical", True,
                f"All {len(AUTH_REQUIRED_ENDPOINTS)} protected endpoints return 401"
            ))

        # ── Check 4: Challenge endpoint validates addresses ─────────
        try:
            # Send an obviously invalid short address
            r = await client.post(
                f"{base}/api/auth/challenge",
                params={"wallet_address": "short"}
            )
            if r.status_code == 422:
                results.append(CheckResult(
                    "Address validation", "critical", True,
                    "Invalid addresses rejected with 422"
                ))
            else:
                results.append(CheckResult(
                    "Address validation", "critical", False,
                    f"Invalid address 'short' accepted (status={r.status_code})"
                ))
        except Exception as e:
            results.append(CheckResult(
                "Address validation", "critical", False,
                f"Could not test: {e}"
            ))

        # ── Check 5: Monitor worker status ──────────────────────────
        monitor_alive = None
        try:
            r = await client.get(f"{base}/api/health")
            data = r.json()
            # Support both old format and new subsystem format
            monitor_alive = data.get("monitor")
            if monitor_alive is None:
                monitor_alive = data.get("subsystems", {}).get("monitor", {}).get("alive")
            # Also check top-level monitor_alive for backward compat
            if monitor_alive is None:
                monitor_alive = data.get("monitor_alive")
        except Exception:
            pass

        if monitor_alive is True or monitor_alive == "running":
            results.append(CheckResult(
                "Monitor worker", "critical", True,
                f"monitor={monitor_alive}"
            ))
        elif monitor_alive is not None:
            results.append(CheckResult(
                "Monitor worker", "critical", False,
                f"monitor={monitor_alive} — should be 'running'"
            ))
        else:
            # Health endpoint doesn't report monitor status — minor
            results.append(CheckResult(
                "Monitor worker visibility", "minor", False,
                "Health endpoint does not report monitor status"
            ))

        # ── Check 6: CORS configuration ─────────────────────────────
        try:
            r = await client.options(
                f"{base}/api/health",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "GET",
                }
            )
            acao = r.headers.get("access-control-allow-origin", "")
            if acao == "*":
                results.append(CheckResult(
                    "CORS restriction", "minor", False,
                    "allow_origin=* — should be restricted to chainwatch domain"
                ))
            elif acao == "https://evil.example.com":
                results.append(CheckResult(
                    "CORS restriction", "minor", False,
                    "Mirrors arbitrary Origin — should restrict to chainwatch domain"
                ))
            else:
                results.append(CheckResult(
                    "CORS restriction", "minor", True,
                    f"allow_origin={acao}"
                ))
        except Exception as e:
            results.append(CheckResult(
                "CORS restriction", "minor", False,
                f"Could not test: {e}"
            ))

    # ── Check 7: DB schema unique constraints (requires direct DB access) ──
    if db_url and asyncpg:
        try:
            conn = await asyncpg.connect(db_url, timeout=10)

            # Check unique constraints
            constraint_results = []
            for constraint_name, table_name in REQUIRED_UNIQUE_CONSTRAINTS:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM pg_constraint
                        WHERE conname = $1 AND conrelid = $2::regclass
                    )
                    """,
                    constraint_name, table_name,
                )
                constraint_results.append((constraint_name, exists))

            missing_constraints = [name for name, exists in constraint_results if not exists]
            if missing_constraints:
                results.append(CheckResult(
                    "Unique constraints (pitfall #19)", "critical", False,
                    f"Missing: {missing_constraints}"
                ))
            else:
                results.append(CheckResult(
                    "Unique constraints (pitfall #19)", "critical", True,
                    f"All {len(REQUIRED_UNIQUE_CONSTRAINTS)} unique constraints exist"
                ))

            # Check columns (pitfall #20)
            missing_columns = []
            for table, columns in REQUIRED_COLUMNS.items():
                for col in columns:
                    exists = await conn.fetchval(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = $1 AND column_name = $2
                        )
                        """,
                        table, col,
                    )
                    if not exists:
                        missing_columns.append(f"{table}.{col}")

            if missing_columns:
                results.append(CheckResult(
                    "Schema column parity (pitfall #20)", "critical", False,
                    f"Missing columns: {missing_columns}"
                ))
            else:
                total_cols = sum(len(cols) for cols in REQUIRED_COLUMNS.values())
                results.append(CheckResult(
                    "Schema column parity (pitfall #20)", "critical", True,
                    f"All {total_cols} required columns present"
                ))

            await conn.close()
        except Exception as e:
            results.append(CheckResult(
                "DB schema checks", "critical", False,
                f"Cannot connect to DB: {e}"
            ))
    else:
        results.append(CheckResult(
            "DB schema checks", "minor", False,
            "Skipped — no DB URL provided or asyncpg not installed"
        ))

    return results


def main():
    parser = argparse.ArgumentParser(description="ChainWatch deployment verification")
    parser.add_argument(
        "--host",
        default="http://localhost:8080",
        help="Base URL of the ChainWatch instance (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="PostgreSQL connection URL for schema checks",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds (default: 10)",
    )
    args = parser.parse_args()

    results = asyncio.run(run_checks(args.host, args.timeout, args.db_url))

    print("=" * 60)
    print("ChainWatch Deployment Verification Report")
    print("=" * 60)
    for r in results:
        print(f"  {r}")

    critical_failed = [r for r in results if not r.passed and r.severity == "critical"]
    minor_failed = [r for r in results if not r.passed and r.severity == "minor"]

    print("=" * 60)
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"Results: {passed}/{total} passed")
    if critical_failed:
        print(f"CRITICAL failures: {len(critical_failed)} — DEPLOYMENT BLOCKED")
        sys.exit(1)
    if minor_failed:
        print(f"Minor failures: {len(minor_failed)} — non-blocking")
        sys.exit(2)
    print("All checks passed ✅")
    sys.exit(0)


if __name__ == "__main__":
    main()
