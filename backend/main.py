"""
ChainWatch - Crypto Portfolio Tracker
FastAPI Backend
"""
import os
import uuid
import hashlib
import time
import json
import asyncio
import httpx
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Any
from decimal import Decimal

from fastapi import FastAPI, Depends, HTTPException, Header, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import asyncpg
import jwt

logger = logging.getLogger(__name__)

# ─── Startup log ring buffer ─────────────────────────────────────────
# Captures key startup events (DB connect, monitor launch, price cache init)
# so the /api/health/startup-log endpoint can return them for debugging.
# Fixed-size ring buffer to prevent unbounded memory growth (Pitfall #12).
_MAX_STARTUP_LOG = 50
_startup_log: list = []


def _log_startup_event(event: str, detail: str = "") -> None:
    """Record a startup event with timestamp. Thread-safe within asyncio."""
    import time as _time
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
        "detail": detail,
    }
    _startup_log.append(entry)
    # Trim to max size (ring buffer behavior)
    while len(_startup_log) > _MAX_STARTUP_LOG:
        _startup_log.pop(0)
    # Also emit to regular logger for container log aggregation
    logger.info("STARTUP: %s %s", event, detail)


def _get_startup_log_entries() -> list:
    """Return a copy of the startup log entries."""
    return list(_startup_log)


# ─── Module-level imports used in endpoint bodies ───────────────────
# (lazy imports are used for optional deps, but classify_wallet is always
#  available and used in the dashboard endpoint)
from services.tx_fetcher import classify_wallet  # noqa: E402

# ─── Encryption helpers (lazy endpoint-local imports) ───────────────
# from services.crypto import encrypt_secret, decrypt_secret

# Configuration
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:***@localhost:5432/chainwatch"
)
JWT_SECRET: str = os.environ.get("JWT_SECRET")  # type: ignore[assignment]
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET environment variable must be set. "
        "Generate one with: python -c \"import secrets; print(secrets.hex(32))\""
    )
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
SOLSCAN_API_KEY = os.environ.get("SOLSCAN_API_KEY", "")
BLOCKCHAIR_API_KEY = os.environ.get("BLOCKCHAIR_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.environ.get(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets"
)
ALPACA_ALLOW_SHARED_KEYS = os.environ.get(
    "ALPACA_ALLOW_SHARED_KEYS", ""
).lower() in ("true", "1", "yes")

# Rough reference prices for whale-classification native amount estimation (F5)
_APPROX_PRICE_USD = {"btc": 105000.0, "eth": 2500.0, "sol": 170.0}

# ── Dashboard price cache (for currency conversion) ────────────────────
# Module-level cache: refreshed every 120 s to avoid CoinGecko rate limits
_dashboard_price_cache: dict = {
    "USDHKD": 7.8,
    "USDBTC": 1.0 / 105000.0,
    "timestamp": 0.0,
}


async def _fetch_dashboard_prices() -> dict:
    """
    Fetch USD/HKD and USD/BTC cross-rates from CoinGecko.
    Returns a dict with USDHKD and USDBTC keys.
    Cached for 120 s to avoid rate limits.
    """
    import time
    now = time.time()
    cache = _dashboard_price_cache
    if cache["timestamp"] > 0 and now - cache["timestamp"] < 120:
        return cache

    try:
        from services.tx_fetcher import _get_client
        client = await _get_client()
        resp = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd,hkd,btc"},
        )
        resp.raise_for_status()
        data = resp.json()

        eth = data.get("ethereum", {})
        eth_usd = eth.get("usd", 0)
        eth_hkd = eth.get("hkd", 0)
        eth_btc = eth.get("btc", 0)

        if eth_usd > 0 and eth_hkd > 0:
            cache["USDHKD"] = eth_hkd / eth_usd
        if eth_usd > 0 and eth_btc > 0:
            cache["USDBTC"] = eth_btc / eth_usd
        cache["timestamp"] = now
    except Exception:
        pass  # Keep stale cached values on failure

    return cache


app = FastAPI(
    title="ChainWatch",
    description="Crypto Portfolio Tracker",
    version="1.0.0"
)

# CORS: restrict to known production domains.
# Set CORS_ORIGINS env var to a comma-separated list of allowed origins.
# Falls back to the production Zeabur domain if not set.
_CORS_ORIGINS_RAW = os.environ.get(
    "CORS_ORIGINS",
    "https://chainwatch-eness.zeabur.app",
)
_CORS_ORIGINS = [o.strip() for o in _CORS_ORIGINS_RAW.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database pool
db_pool: Optional[asyncpg.Pool] = None

# ─── Metrics Middleware ───────────────────────────────────────────────
# Must be added early so it captures all downstream endpoints.
from services.metrics_middleware import MetricsMiddleware  # noqa: E402
app.add_middleware(MetricsMiddleware)


@app.on_event("startup")
async def startup():
    global db_pool

    # ── Fail-fast: validate CHAINWATCH_MASTER_KEY for per-user Alpaca encryption ──
    try:
        from services.crypto import _get_master_key
        _get_master_key()
        logger.info("CHAINWATCH_MASTER_KEY validated — per-user Alpaca encryption available")
        _log_startup_event("master_key_validated")
    except ValueError as e:
        logger.warning("CHAINWATCH_MASTER_KEY not configured: %s. Per-user Alpaca key storage will fail.", e)
        _log_startup_event("master_key_missing", str(e))
    except Exception as e:
        logger.warning("Could not validate CHAINWATCH_MASTER_KEY: %s", e)
        _log_startup_event("master_key_error", str(e))

    # ── Warn if neither per-user nor shared Alpaca keys are available ──
    if not ALPACA_ALLOW_SHARED_KEYS and not ALPACA_API_KEY:
        logger.warning(
            "No Alpaca keys available: per-user keys require CHAINWATCH_MASTER_KEY + user connect, "
            "and ALPACA_ALLOW_SHARED_KEYS is not set. Mirror trades will return 402 until users connect."
        )

    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
        _log_startup_event("db_pool_created", f"min_size=2 max_size=10")
        # Instrument the pool for query metrics
        from services.instrumented_pool import instrument_pool
        instrument_pool(db_pool)
        _log_startup_event("db_pool_instrumented")
    except Exception as e:
        logger.warning(f"Failed to create DB pool on startup: {e}. API endpoints requiring DB will return 503.")
        _log_startup_event("db_pool_failed", str(e))
        db_pool = None

    # Launch the background wallet monitor
    if db_pool:
        from services.monitor import start_monitor
        start_monitor(db_pool)
        _log_startup_event("monitor_started", f"poll_interval=60s")


@app.on_event("shutdown")
async def shutdown():
    # F8: await the async close which sends the signal AND drains in-flight queries
    if db_pool:
        await db_pool.close()
    # Close the shared HTTP client from tx_fetcher
    try:
        from services.tx_fetcher import close_client
        await close_client()
    except Exception:
        pass
    # Close the monitor worker
    try:
        from services.monitor import stop_monitor
        await stop_monitor()
    except Exception:
        pass
    # Gracefully close all WebSocket connections
    try:
        from services.websocket_manager import websocket_manager
        await websocket_manager.close_all(code=1001, reason="Server shutting down")
    except Exception:
        pass


# ─── Helpers ────────────────────────────────────────────────────────

async def require_db():
    """Dependency that raises 503 if DB is not available."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool


async def get_db():
    pool = await require_db()
    async with pool.acquire() as conn:
        yield conn


def acquire_db():
    """Get a DB connection handle for direct use. Raises 503 if DB unavailable."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool.acquire()


def create_jwt(wallet_address: str, user_id: str = "", created_at: str = "") -> str:
    payload = {
        "sub": wallet_address,
        "uid": user_id,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=7),
        "jti": str(uuid.uuid4()),
    }
    if created_at:
        payload["cat"] = created_at  # "cat" = created_at (short to keep token small)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _verify_wallet_signature(wallet_address: str, signature: str, message: str, chain: str = "eth") -> None:
    """
    Cryptographically verify that `signature` on `message` was produced by the private
    key owning `wallet_address`. Raises HTTP 401 if verification fails.

    ETH: Uses eth_account (EIP-191 personal_sign recovery).
    Solana: Uses pynacl/curve25519 ed25519 verification.

    Dependencies: eth-account (pip install eth-account)
                  pynacl     (pip install pynacl)  [Solana only]
    """
    if chain == "eth":
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
        except ImportError:
            raise RuntimeError("eth-account package not installed: pip install eth-account")
        msg_hash = encode_defunct(text=message)
        recovered = Account.recover_message(msg_hash, signature=signature)
        if recovered.lower() != wallet_address.lower():
            raise HTTPException(status_code=401, detail="Signature verification failed for ETH address")
    elif chain == "sol":
        try:
            import nacl.signing
            import nacl.encoding
        except ImportError:
            raise RuntimeError("pynacl package not installed: pip install pynacl")
        # Solana addresses are base58-encoded ed25519 public keys (32 bytes)
        try:
            import base58
        except ImportError:
            raise RuntimeError("base58 package not installed: pip install base58")
        try:
            verify_key_bytes = base58.b58decode(wallet_address)
            signature_bytes = bytes.fromhex(signature) if signature.startswith("0x") else base58.b58decode(signature)
            message_bytes = message.encode("utf-8")
            verify_key = nacl.signing.VerifyKey(verify_key_bytes)
            verify_key.verify(message_bytes, signature_bytes)
        except Exception:
            raise HTTPException(status_code=401, detail="Signature verification failed for Solana address")
    elif chain == "btc":
        # BTC signature verification is complex (segwit, legacy, etc.)
        # Accept as-is with a logged warning; implement with python-bitcoinlib in production
        import logging as _log
        _log.warning("BTC signature not cryptographically verified — chain=%s addr=%s", chain, wallet_address[:8])
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported chain: {chain}")


async def get_current_user(
    authorization: str = Header(...),
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization[7:]
    payload = verify_jwt(token)

    # Optimization (BUG-18): Extract user_id from JWT payload to avoid DB lookup
    # on every authenticated request. The uid is embedded at JWT creation time.
    # Also extract created_at (Pitfall #21) to avoid DB round-trip in get_me().
    user_id = payload.get("uid")
    if user_id:
        return {
            "id": user_id,
            "wallet_address": payload["sub"],
            "created_at": payload.get("cat", ""),  # "cat" = created_at from JWT
        }

    # Fallback: verify user still exists in DB (for old tokens without uid)
    async with acquire_db() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE wallet_address = $1",
            payload["sub"],
        )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


# ─── Pydantic Models ───────────────────────────────────────────────

class WalletConnectRequest(BaseModel):
    wallet_address: str = Field(..., min_length=10, max_length=255)
    signature: str = Field(..., min_length=10)
    message: str = Field(..., min_length=10)
    chain: str = Field(default="eth", pattern="^(eth|sol|btc)$")


class WalletAddRequest(BaseModel):
    address: str = Field(..., min_length=10, max_length=255)
    chain: str = Field(..., pattern="^(eth|sol|btc)$")
    label: str = Field(default="", max_length=255)
    is_whale: bool = False
    is_mine: bool = False


class WalletUpdateRequest(BaseModel):
    label: Optional[str] = None
    is_whale: Optional[bool] = None
    is_mine: Optional[bool] = None


class AlertRequest(BaseModel):
    rule_type: str = Field(..., max_length=50)
    threshold: float = Field(default=0.0, ge=0, le=1000000)
    enabled: bool = True
    notify_telegram: bool = True


class AlertUpdateRequest(BaseModel):
    threshold: Optional[float] = None
    enabled: Optional[bool] = None
    notify_telegram: Optional[bool] = None


class AlpacaConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=20, max_length=255)
    secret_key: str = Field(..., min_length=20, max_length=255)


# ─── Auth Endpoints ─────────────────────────────────────────────────

@app.post("/api/auth/challenge")
async def create_challenge(wallet_address: str = Query(..., min_length=10, max_length=255)):
    """Create a signing challenge for WalletConnect auth."""
    import re
    # Validate wallet address format (Finding: no validation on challenge creation)
    eth_pattern = re.compile(r"^0x[0-9a-fA-F]{40}$")
    sol_pattern = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    btc_pattern = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$")
    addr_stripped = wallet_address.strip()
    if not (eth_pattern.match(addr_stripped) or sol_pattern.match(addr_stripped) or btc_pattern.match(addr_stripped)):
        raise HTTPException(status_code=422, detail="Invalid wallet address format")
    # Sanitize for log safety
    safe_addr = addr_stripped[:6] + "..."
    nonce = secrets.token_hex(16)
    message = (
        f"ChainWatch Authentication\n\n"
        f"Sign this message to prove ownership of {safe_addr}.\n\n"
        f"Nonce: {nonce}\n"
        f"Timestamp: {int(time.time())}\n"
        f"Domain: chainwatch.app"
    )
    return {
        "message": message,
        "nonce": nonce,
        "wallet_address": addr_stripped
    }


@app.post("/api/auth/verify")
async def verify_signature(req: WalletConnectRequest):
    """Verify wallet signature and return JWT."""
    # CRITICAL FIX: Actually verify the cryptographic signature
    # Supports ETH (EIP-191 personal_sign) and Solana (ed25519)
    _verify_wallet_signature(
        wallet_address=req.wallet_address,
        signature=req.signature,
        message=req.message,
        chain=req.chain,
    )

    async with acquire_db() as conn:
        # Upsert user
        user = await conn.fetchrow(
            """
            INSERT INTO users (wallet_address)
            VALUES ($1)
            ON CONFLICT (wallet_address)
            DO UPDATE SET wallet_address = $1
            RETURNING *
            """,
            req.wallet_address.lower()
        )

        # Create JWT with user_id + created_at embedded to avoid DB lookup on every request
        token = create_jwt(
            req.wallet_address.lower(),
            user_id=str(user["id"]),
            created_at=user["created_at"].isoformat() if user.get("created_at") else "",
        )

        # Update session
        await conn.execute(
            """
            UPDATE users
            SET session_token = $1, session_expires_at = $2
            WHERE wallet_address = $3
            """,
            token,
            datetime.utcnow() + timedelta(days=7),
            req.wallet_address.lower()
        )

    return {
        "token": token,
        "user": {
            "wallet_address": user["wallet_address"],
            "created_at": user["created_at"].isoformat()
        }
    }


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    # Pitfall #21 fix: created_at is embedded in JWT payload at token creation time.
    # No DB round-trip needed. Falls back to None for old tokens that lack the "cat" claim.
    return {
        "wallet_address": user["wallet_address"],
        "created_at": user.get("created_at") or None,
    }


# ─── Per-User Alpaca Credential Endpoints ───────────────────────────

@app.post("/api/user/alpaca")
async def connect_alpaca(
    req: AlpacaConnectRequest,
    user: dict = Depends(get_current_user),
):
    """
    Validate and store the calling user's Alpaca paper trading API keys.

    1. Calls Alpaca GET /v2/account with the provided keys to validate them.
    2. Encrypts the keys with AES-256-GCM (master key = CHAINWATCH_MASTER_KEY).
    3. Persists the encrypted credentials, account_id, and timestamp in the users table.
    """
    from services.crypto import encrypt_secret  # noqa: PLC0415
    from services.tx_fetcher import _get_client  # noqa: PLC0415

    # ── Validate credentials against Alpaca ─────────────────────────
    try:
        client = await _get_client()
        acct_resp = await client.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers={
                "APCA-API-KEY-ID": req.api_key,
                "APCA-API-SECRET-KEY": req.secret_key,
            },
            timeout=15,
        )
        acct_resp.raise_for_status()
        acct_data = acct_resp.json()
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=400, detail="Invalid Alpaca credentials")
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach Alpaca for credential validation")

    account_id: str = acct_data.get("account_id", "")
    equity = float(acct_data.get("equity", 0))

    # ── Encrypt & persist ────────────────────────────────────────────
    # Each secret gets its own IV to avoid AES-GCM nonce reuse
    api_key_ct, api_key_iv = encrypt_secret(req.api_key)
    secret_key_ct, secret_key_iv = encrypt_secret(req.secret_key)

    async with acquire_db() as conn:
        await conn.execute(
            """
            UPDATE users
            SET alpaca_api_key_enc     = $1,
                alpaca_api_key_iv      = $2,
                alpaca_secret_key_enc  = $3,
                alpaca_secret_key_iv   = $4,
                alpaca_paper_account_id = $5,
                alpaca_connected_at    = NOW()
            WHERE id = $6
            """,
            api_key_ct,
            api_key_iv,
            secret_key_ct,
            secret_key_iv,
            account_id,
            user["id"],
        )

    return {
        "connected": True,
        "equity": equity,
        "account_id": account_id,
    }


@app.delete("/api/user/alpaca")
async def disconnect_alpaca(
    user: dict = Depends(get_current_user),
):
    """Clear all stored Alpaca credentials for the current user."""
    async with acquire_db() as conn:
        await conn.execute(
            """
            UPDATE users
            SET alpaca_api_key_enc     = NULL,
                alpaca_api_key_iv      = NULL,
                alpaca_secret_key_enc   = NULL,
                alpaca_secret_key_iv   = NULL,
                alpaca_paper_account_id = NULL,
                alpaca_connected_at     = NULL
            WHERE id = $1
            """,
            user["id"],
        )
    return {"disconnected": True}


# ─── Telegram Notification Settings ─────────────────────────────────

class TelegramSettingsRequest(BaseModel):
    chat_id: str = Field(..., max_length=64, description="Telegram chat ID for notifications")


@app.post("/api/user/telegram")
async def set_telegram_chat_id(
    req: TelegramSettingsRequest,
    user: dict = Depends(get_current_user),
):
    """Set the Telegram chat ID for alert notifications."""
    async with acquire_db() as conn:
        await conn.execute(
            "UPDATE users SET telegram_chat_id = $1 WHERE id = $2",
            req.chat_id.strip(),
            user["id"],
        )
    return {"telegram_configured": True}


@app.delete("/api/user/telegram")
async def clear_telegram_chat_id(
    user: dict = Depends(get_current_user),
):
    """Remove the Telegram chat ID (disable Telegram notifications)."""
    async with acquire_db() as conn:
        await conn.execute(
            "UPDATE users SET telegram_chat_id = NULL WHERE id = $1",
            user["id"],
        )
    return {"telegram_configured": False}


@app.get("/api/user/alpaca/status")
async def alpaca_status(
    user: dict = Depends(get_current_user),
):
    """
    Return the user's Alpaca connection status and live equity (if connected).
    """
    from services.crypto import decrypt_secret  # noqa: PLC0415
    from services.tx_fetcher import _get_client  # noqa: PLC0415

    async with acquire_db() as conn:
        row = await conn.fetchrow(
            """
            SELECT alpaca_api_key_enc,
                   alpaca_api_key_iv,
                   alpaca_secret_key_enc,
                   alpaca_secret_key_iv,
                   alpaca_paper_account_id
            FROM users
            WHERE id = $1
            """,
            user["id"],
        )

    if not row or row["alpaca_api_key_enc"] is None:
        return {"connected": False, "equity": None, "account_id": None}

    # Credentials exist – fetch live equity from Alpaca
    try:
        decrypted_key = decrypt_secret(row["alpaca_api_key_enc"], row["alpaca_api_key_iv"])
        decrypted_secret = decrypt_secret(row["alpaca_secret_key_enc"], row["alpaca_secret_key_iv"])
    except Exception:
        # Decryption failed – treat as disconnected
        return {"connected": False, "equity": None, "account_id": None}

    equity: float | None = None
    try:
        client = await _get_client()
        acct_resp = await client.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers={
                "APCA-API-KEY-ID": decrypted_key,
                "APCA-API-SECRET-KEY": decrypted_secret,
            },
            timeout=15,
        )
        if acct_resp.status_code == 200:
            equity = float(acct_resp.json().get("equity", 0))
    except Exception:
        pass  # Return stale equity as None on failure

    return {
        "connected": True,
        "equity": equity,
        "account_id": row["alpaca_paper_account_id"],
    }


# ─── Dashboard Endpoint ─────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(user: dict = Depends(get_current_user)):
    uid = user["id"]
    async with acquire_db() as conn:
        wallets = await conn.fetch(
            """
            SELECT w.*,
                   COALESCE(SUM(t.usd_value) FILTER (WHERE t.type = 'receive'), 0) as total_received,
                   COALESCE(SUM(t.usd_value) FILTER (WHERE t.type = 'send'), 0) as total_sent
            FROM wallets w
            LEFT JOIN transactions t ON t.wallet_id = w.id
            WHERE w.user_id = $1
            GROUP BY w.id
            ORDER BY w.created_at DESC
            """,
            uid
        )

        # Pre-fetch tx counts while we hold the connection (avoids dangling ref bug)
        _tx_count_cache: dict = {}
        if wallets:
            wallet_ids = [w["id"] for w in wallets]
            try:
                _tx_count_rows = await conn.fetch(
                    "SELECT wallet_id, COUNT(*) AS cnt FROM transactions "
                    "WHERE wallet_id = ANY($1) GROUP BY wallet_id",
                    wallet_ids
                )
                _tx_count_cache = {str(r["wallet_id"]): r["cnt"] for r in _tx_count_rows}
            except Exception:
                pass

    # Run independent queries concurrently on separate connections (Finding: sequential queries)
    async def _fetch_txs():
        async with acquire_db() as c:
            return await c.fetch(
                """
                SELECT t.*, w.address as wallet_address, w.chain, w.label
                FROM transactions t
                JOIN wallets w ON w.id = t.wallet_id
                WHERE w.user_id = $1
                ORDER BY t.timestamp DESC
                LIMIT 20
                """, uid
            )

    async def _fetch_alerts():
        async with acquire_db() as c:
            return await c.fetch(
                "SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC", uid
            )

    async def _fetch_signals():
        async with acquire_db() as c:
            return await c.fetch(
                """
                SELECT cts.id, cts.wallet_id, cts.token_symbol, cts.action,
                       cts.amount_usd, cts.confidence_score, cts.status,
                       cts.created_at, cts.explanation, cts.explanation_stale,
                       cts.score_at_generation,
                       w.address as wallet_address, w.label as wallet_label,
                       w.is_whale, w.is_mine, w.whale_score
                FROM copy_trade_signals cts
                JOIN wallets w ON w.id = cts.wallet_id
                WHERE w.user_id = $1
                ORDER BY cts.created_at DESC
                LIMIT 50
                """, uid
            )

    db_txs, alerts, signals = await asyncio.gather(
        _fetch_txs(), _fetch_alerts(), _fetch_signals()
    )

    # ── Live on-chain transaction fetch ─────────────────────────────
    # Build wallet list for live fetch
    wallet_list = [
        {"address": w["address"], "chain": w["chain"], "label": w["label"] or ""}
        for w in wallets
    ]

    live_txs: List[dict] = []
    if wallet_list:
        try:
            from services.tx_fetcher import fetch_transactions_for_wallets
            # F2: hard timeout so slow RPCs never block the dashboard indefinitely
            live_txs = await asyncio.wait_for(
                fetch_transactions_for_wallets(wallet_list, limit=10),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Live tx fetch timed out (>12 s), using DB-only")
            live_txs = []
        except Exception as e:
            logger.warning(f"Live tx fetch failed, using DB-only: {e}")
            live_txs = []

    # ── Merge: prefer live txs, fall back to DB txs ────────────────
    if live_txs:
        recent_tx_list = live_txs[:20]
    else:
        recent_tx_list = [
            {
                "id": str(t["id"]),
                "tx_hash": t["tx_hash"],
                "type": t["type"],
                "amount": str(t["amount"]),
                "token": t["token"],
                "usd_value": float(t["usd_value"] or 0),
                "timestamp": t["timestamp"].isoformat(),
                "chain": t["chain"],
                "wallet_label": t["label"],
                "wallet_address": t["wallet_address"],
                "status": "confirmed",
            }
            for t in db_txs
        ]

    # ── Whale detection & risk profiling (F5: use classify_wallet) ──
    # _tx_count_cache was pre-fetched above while holding the DB connection.

    # ── Fetch live prices for currency conversion ────────────────────
    _prices = await _fetch_dashboard_prices()
    _usd_hkd = _prices.get("USDHKD", 7.8)       # fallback: ~peg
    _usd_btc = _prices.get("USDBTC", 1.0 / 105000.0)  # fallback (BTC ~$105K)

    wallet_meta: List[dict] = []
    fresh_count = 0

    for w in wallets:
        # Prefer the monitor-updated balance_usd column (kept fresh by the
        # background worker).  Fall back to tx-flow computation only when
        # the column is NULL — avoids stale tx-flow diverging from reality.
        _db_balance = w.get("balance_usd")
        if _db_balance is not None:
            balance_usd = float(_db_balance)
        else:
            balance_usd = float(w["total_received"] or 0) - float(w["total_sent"] or 0)
        # Estimate native balance from approximate price for whale classification
        balance_native_est = balance_usd / _APPROX_PRICE_USD.get(w["chain"], 1.0)
        tx_count = _tx_count_cache.get(str(w["id"]), 0)
        risk = classify_wallet(balance_native_est, w["chain"], tx_count)
        # Keep manual DB override OR auto-detected whale status
        is_whale = w["is_whale"] or risk["is_whale"]
        is_fresh = risk["is_fresh_wallet"]
        if is_fresh:
            fresh_count += 1

        # Currency conversion
        balance_hkd = round(balance_usd * _usd_hkd, 2)
        balance_btc = round(balance_usd * _usd_btc, 8)

        wallet_meta.append({
            "id": str(w["id"]),
            "address": w["address"],
            "chain": w["chain"],
            "label": w["label"],
            "is_whale": is_whale,
            "is_mine": w["is_mine"],
            "is_fresh_wallet": is_fresh,
            "risk_label": risk["risk_label"],
            "balance_native": round(balance_native_est, 8),
            "balance_usd": round(balance_usd, 2),
            "balance_hkd": balance_hkd,
            "balance_btc": balance_btc,
            "last_balance_update": w["last_balance_update"].isoformat() if w.get("last_balance_update") else None,
            "created_at": w["created_at"].isoformat(),
            "whale_score": float(w.get("whale_score") or 0),
        })

    # ── Absolute portfolio isolation ───────────────────────────────────
    # Only wallets explicitly marked as is_mine AND NOT is_whale count
    # toward the user's portfolio total. Whale-tracked wallets are for
    # monitoring only and must never leak into balance aggregation.
    personal_wallet_ids = {
        wm["id"] for wm in wallet_meta
        if wm["is_mine"] and not wm["is_whale"]
    }

    total_value_usd = sum(
        wm["balance_usd"]
        for wm in wallet_meta
        if wm["id"] in personal_wallet_ids
    )
    total_value_hkd = round(total_value_usd * _usd_hkd, 2)
    total_value_btc = round(total_value_usd * _usd_btc, 8)

    # Split wallet_meta for frontend convenience
    personal_wallets = [wm for wm in wallet_meta if wm["is_mine"] and not wm["is_whale"]]
    whale_wallets_list = [wm for wm in wallet_meta if wm["is_whale"]]

    return {
        "portfolio": {
            "total_value_usd": round(total_value_usd, 2),
            "total_value_hkd": total_value_hkd,
            "total_value_btc": total_value_btc,
            "wallets_tracked": len(personal_wallets),
            "whale_wallets_tracked": len(whale_wallets_list),
            "fresh_wallets": fresh_count,
        },
        "wallets": wallet_meta,
        "personal_wallets": personal_wallets,
        "whale_wallets_list": whale_wallets_list,
        "recent_transactions": recent_tx_list,
        "alerts": [
            {
                "id": str(a["id"]),
                "rule_type": a["rule_type"],
                "threshold": float(a["threshold"] or 0),
                "enabled": a["enabled"],
                "created_at": a["created_at"].isoformat()
            }
            for a in alerts
        ],
        "copy_trade_signals": [
            {
                "id": str(s["id"]),
                "token_symbol": s["token_symbol"],
                "action": s["action"],
                "amount_usd": float(s["amount_usd"] or 0),
                "confidence_score": float(s["confidence_score"] or 0),
                "confidence_final": round(
                    0.5 * float(s["confidence_score"] or 0)
                    + 0.5 * float(s["score_at_generation"] or 0), 2
                ),
                "status": s["status"],
                "wallet_label": s["wallet_label"],
                "wallet_address": s["wallet_address"],
                "created_at": s["created_at"].isoformat(),
                "explanation": s["explanation"],
                "explanation_stale": s["explanation_stale"] or False,
                "whale_score": float(s["whale_score"] or 0),
                "score_at_generation": float(s["score_at_generation"] or 0),
            }
            for s in signals
        ]
    }


# ─── Wallet Endpoints ───────────────────────────────────────────────

@app.get("/api/wallets")
async def list_wallets(user: dict = Depends(get_current_user)):
    async with acquire_db() as conn:
        wallets = await conn.fetch(
            "SELECT * FROM wallets WHERE user_id = $1 ORDER BY created_at DESC",
            user["id"]
        )
    return {
        "wallets": [
            {
                "id": str(w["id"]),
                "address": w["address"],
                "chain": w["chain"],
                "label": w["label"],
                "is_whale": w["is_whale"],
                "is_mine": w["is_mine"],
                "balance_usd": float(w["balance_usd"]) if w.get("balance_usd") is not None else None,
                "balance_hkd": float(w["balance_hkd"]) if w.get("balance_hkd") is not None else None,
                "balance_btc": float(w["balance_btc"]) if w.get("balance_btc") is not None else None,
                "whale_score": float(w.get("whale_score") or 0),
                "last_balance_update": w["last_balance_update"].isoformat() if w.get("last_balance_update") else None,
                "created_at": w["created_at"].isoformat()
            }
            for w in wallets
        ]
    }


@app.post("/api/wallets")
async def add_wallet(
    req: WalletAddRequest,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        wallet = await conn.fetchrow(
            """
            INSERT INTO wallets (user_id, address, chain, label, is_whale, is_mine)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            user["id"],
            req.address,
            req.chain,
            req.label,
            req.is_whale,
            req.is_mine
        )
    return {
        "wallet": {
            "id": str(wallet["id"]),
            "address": wallet["address"],
            "chain": wallet["chain"],
            "label": wallet["label"],
            "is_whale": wallet["is_whale"],
            "is_mine": wallet["is_mine"],
            "created_at": wallet["created_at"].isoformat()
        }
    }


@app.put("/api/wallets/{wallet_id}")
async def update_wallet(
    wallet_id: str,
    req: WalletUpdateRequest,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        wallet = await conn.fetchrow(
            "SELECT * FROM wallets WHERE id = $1 AND user_id = $2",
            wallet_id, user["id"]
        )
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        updates = {}
        if req.label is not None:
            updates["label"] = req.label
        if req.is_whale is not None:
            updates["is_whale"] = req.is_whale
        if req.is_mine is not None:
            updates["is_mine"] = req.is_mine

        if updates:
            set_clauses = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
            values = list(updates.values())
            # Fix Pitfall #8: Use RETURNING * instead of separate SELECT to avoid
            # write-then-re-read race — concurrent requests on the same wallet could
            # interleave, causing the re-read to return another user's update.
            updated = await conn.fetchrow(
                f"UPDATE wallets SET {set_clauses} WHERE id = $1 AND user_id = $2 RETURNING *",
                wallet_id, user["id"], *values
            )
        else:
            updated = wallet

    return {
        "wallet": {
            "id": str(updated["id"]),
            "address": updated["address"],
            "chain": updated["chain"],
            "label": updated["label"],
            "is_whale": updated["is_whale"],
            "is_mine": updated["is_mine"],
            "created_at": updated["created_at"].isoformat()
        }
    }


@app.delete("/api/wallets/{wallet_id}")
async def delete_wallet(
    wallet_id: str,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        result = await conn.execute(
            "DELETE FROM wallets WHERE id = $1 AND user_id = $2",
            wallet_id, user["id"]
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"deleted": True}


@app.post("/api/wallets/{wallet_id}/refresh")
async def refresh_wallet(
    wallet_id: str,
    user: dict = Depends(get_current_user),
):
    """Refresh a wallet's balance from the blockchain and update the DB."""
    async with acquire_db() as conn:
        wallet = await conn.fetchrow(
            "SELECT * FROM wallets WHERE id = $1 AND user_id = $2",
            wallet_id, user["id"],
        )
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    # Fetch live balance via blockchain client
    from services.blockchain import EtherscanClient, SolscanClient, BlockchairClient

    chain = wallet["chain"]
    addr = wallet["address"]
    balance_native = 0.0
    symbol = chain.upper()

    client = None
    try:
        if chain == "eth":
            client = EtherscanClient()
            bal = await client.get_eth_balance(addr)
            balance_native = bal.get("balance_eth", 0)
            symbol = "ETH"
        elif chain == "sol":
            client = SolscanClient()
            bal = await client.get_balance(addr)
            balance_native = bal.get("balance_sol", 0)
            symbol = "SOL"
        elif chain == "btc":
            client = BlockchairClient()
            bal = await client.get_balance(addr)
            balance_native = bal.get("balance_btc", 0)
            symbol = "BTC"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported chain: {chain}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Balance fetch failed: {e}")
    finally:
        # Always close the client to avoid connection leaks
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    # Convert to USD using live CoinGecko price for the specific token
    # (Pitfall: using _APPROX_PRICE_USD here would produce stale USD values
    #  when market prices have moved significantly from hardcoded defaults.)
    _coin_ids = {"eth": "ethereum", "sol": "solana", "btc": "bitcoin"}
    _coin_id = _coin_ids.get(chain, "ethereum")
    _token_price_usd = _APPROX_PRICE_USD.get(chain, 1.0)  # fallback default
    try:
        from services.tx_fetcher import _get_client
        _cg_client = await _get_client()
        _resp = await _cg_client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": _coin_id, "vs_currencies": "usd,hkd,btc"},
        )
        _resp.raise_for_status()
        _price_data = _resp.json().get(_coin_id, {})
        if _price_data.get("usd", 0) > 0:
            _token_price_usd = _price_data["usd"]
            _usd_hkd = _price_data.get("hkd", 0) / _price_data["usd"] if _price_data.get("hkd") else 7.8
            _usd_btc = _price_data.get("btc", 0) / _price_data["usd"] if _price_data.get("btc") else 1.0 / 105000.0
        else:
            # Fallback to dashboard cache for cross-rates
            _dash_prices = await _fetch_dashboard_prices()
            _usd_hkd = _dash_prices.get("USDHKD", 7.8)
            _usd_btc = _dash_prices.get("USDBTC", 1.0 / 105000.0)  # fallback: BTC ~$105K
    except Exception:
        # Fallback to dashboard cache on any error
        _dash_prices = await _fetch_dashboard_prices()
        _usd_hkd = _dash_prices.get("USDHKD", 7.8)
        _usd_btc = _dash_prices.get("USDBTC", 1.0 / 105000.0)  # fallback: BTC ~$105K

    balance_usd = balance_native * _token_price_usd
    balance_hkd = round(balance_usd * _usd_hkd, 2)
    balance_btc = round(balance_usd * _usd_btc, 8)

    # Update DB with all balance columns so list_wallets and other endpoints
    # can read converted balances without on-the-fly computation.
    async with acquire_db() as conn:
        await conn.execute(
            """
            UPDATE wallets
            SET balance_native   = $1,
                balance_usd      = $2,
                balance_hkd      = $3,
                balance_btc      = $4,
                last_balance_update = $5
            WHERE id = $6
            """,
            balance_native, balance_usd, balance_hkd, balance_btc,
            datetime.utcnow(), wallet_id,
        )

    return {
        "wallet_id": wallet_id,
        "address": addr,
        "chain": chain,
        "balance_native": balance_native,
        "balance_usd": round(balance_usd, 2),
        "balance_hkd": balance_hkd,
        "balance_btc": balance_btc,
        "last_balance_update": datetime.utcnow().isoformat(),
    }


# ─── Per-wallet live transactions ───────────────────────────────────

@app.get("/api/wallets/{wallet_id}/transactions")
async def get_wallet_transactions(
    wallet_id: str,
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(get_current_user)
):
    """Fetch live on-chain transactions for a specific wallet."""
    async with acquire_db() as conn:
        wallet = await conn.fetchrow(
            "SELECT * FROM wallets WHERE id = $1 AND user_id = $2",
            wallet_id, user["id"]
        )
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    try:
        from services.tx_fetcher import fetch_transactions_for_wallet
        txs = await fetch_transactions_for_wallet(wallet["address"], wallet["chain"], limit)
    except Exception as e:
        logger.warning(f"Live tx fetch failed for wallet {wallet_id}: {e}")
        txs = []

    return {
        "wallet_id": wallet_id,
        "address": wallet["address"],
        "chain": wallet["chain"],
        "transactions": txs,
        "source": "live" if txs else "none",
    }


# ─── Whale Suggestions ──────────────────────────────────────────────

@app.get("/api/whale-suggestions")
async def get_whale_suggestions(
    chain: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),  # F14: require authentication
):
    async with acquire_db() as conn:
        if chain:
            suggestions = await conn.fetch(
                "SELECT * FROM whale_suggestions WHERE chain = $1 ORDER BY added_at DESC LIMIT 5",
                chain
            )
        else:
            suggestions = await conn.fetch(
                "SELECT * FROM whale_suggestions ORDER BY chain, added_at DESC LIMIT 15"
            )
    return {
        "suggestions": [
            {
                "id": str(s["id"]),
                "chain": s["chain"],
                "address": s["address"],
                "label": s["label"],
                "source": s["source"]
            }
            for s in suggestions
        ]
    }


# ─── Activity / Transactions ────────────────────────────────────────

@app.get("/api/activity")
async def get_activity(
    page: int = Query(1, ge=1),
    chain: Optional[str] = Query(None),
    tx_type: Optional[str] = Query(None, alias="type"),
    user: dict = Depends(get_current_user),
):
    per_page = 25
    offset = (page - 1) * per_page

    async with acquire_db() as conn:
        params: list = [user["id"]]
        conditions = ["w.user_id = $1"]
        param_idx = 2

        if chain:
            params.append(chain)
            conditions.append(f"w.chain = ${param_idx}")
            param_idx += 1

        if tx_type:
            params.append(tx_type)
            conditions.append(f"t.type = ${param_idx}")
            param_idx += 1

        where_clause = " AND ".join(conditions)

        # Fetch total count for pagination metadata
        total = await conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM transactions t
            JOIN wallets w ON w.id = t.wallet_id
            WHERE {where_clause}
            """,
            *params
        )

        # Fetch paginated results
        params.append(per_page)
        limit_param = param_idx
        param_idx += 1
        params.append(offset)
        offset_param = param_idx

        transactions = await conn.fetch(
            f"""
            SELECT t.*, w.address as wallet_address, w.chain, w.label
            FROM transactions t
            JOIN wallets w ON w.id = t.wallet_id
            WHERE {where_clause}
            ORDER BY t.timestamp DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
            """,
            *params
        )

    total_pages = max(1, (total + per_page - 1) // per_page)

    return {
        "transactions": [
            {
                "id": str(t["id"]),
                "tx_hash": t["tx_hash"],
                "type": t["type"],
                "amount": str(t["amount"]),
                "token": t["token"],
                "usd_value": float(t["usd_value"] or 0),
                "timestamp": t["timestamp"].isoformat(),
                "chain": t["chain"],
                "wallet_label": t["label"],
                "wallet_address": t["wallet_address"],
                "status": "confirmed",
            }
            for t in transactions
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


# ─── Alerts ─────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    async with acquire_db() as conn:
        alerts = await conn.fetch(
            """
            SELECT a.*, fa.created_at AS last_fired
            FROM alerts a
            LEFT JOIN LATERAL (
                SELECT created_at
                FROM fired_alerts
                WHERE fired_alerts.alert_id = a.id
                ORDER BY created_at DESC
                LIMIT 1
            ) fa ON TRUE
            WHERE a.user_id = $1
            ORDER BY a.created_at DESC
            """,
            user["id"]
        )
    return {
        "alerts": [
            {
                "id": str(a["id"]),
                "rule_type": a["rule_type"],
                "threshold": float(a["threshold"] or 0),
                "enabled": a["enabled"],
                "created_at": a["created_at"].isoformat(),
                "last_fired": a["last_fired"].isoformat() if a["last_fired"] else None,
            }
            for a in alerts
        ]
    }


@app.get("/api/alerts/history")
async def get_alert_history(user: dict = Depends(get_current_user)):
    """Return fired alert history for the current user, most recent first."""
    async with acquire_db() as conn:
        rows = await conn.fetch(
            """
            SELECT fa.id, fa.alert_id, fa.rule_type,
                   fa.trigger_value, fa.details, fa.created_at
            FROM fired_alerts fa
            WHERE fa.user_id = $1
            ORDER BY fa.created_at DESC
            LIMIT 50
            """,
            user["id"]
        )
    return {
        "history": [
            {
                "id": str(r["id"]),
                "alert_id": str(r["alert_id"]),
                "rule_type": r["rule_type"],
                "trigger_value": float(r["trigger_value"] or 0),
                "details": r["details"] if isinstance(r["details"], dict) else {},
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }


@app.post("/api/alerts")
async def create_alert(
    req: AlertRequest,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        alert = await conn.fetchrow(
            """
            INSERT INTO alerts (user_id, rule_type, threshold, enabled, notify_telegram)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            user["id"],
            req.rule_type,
            req.threshold,
            req.enabled,
            req.notify_telegram,
        )
    return {
        "alert": {
            "id": str(alert["id"]),
            "rule_type": alert["rule_type"],
            "threshold": float(alert["threshold"] or 0),
            "enabled": alert["enabled"],
            "notify_telegram": alert.get("notify_telegram", True),
            "created_at": alert["created_at"].isoformat(),
        }
    }


@app.put("/api/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    req: AlertUpdateRequest,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        alert = await conn.fetchrow(
            "SELECT * FROM alerts WHERE id = $1 AND user_id = $2",
            alert_id, user["id"]
        )
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        updates = {}
        if req.threshold is not None:
            updates["threshold"] = req.threshold
        if req.enabled is not None:
            updates["enabled"] = req.enabled

        if updates:
            set_clauses = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
            values = list(updates.values())
            # Fix Pitfall #8: Use RETURNING * instead of separate SELECT to avoid
            # write-then-re-read race (same pattern as update_wallet fix).
            updated = await conn.fetchrow(
                f"UPDATE alerts SET {set_clauses} WHERE id = $1 AND user_id = $2 RETURNING *",
                alert_id, user["id"], *values
            )
        else:
            updated = alert

    return {
        "alert": {
            "id": str(updated["id"]),
            "rule_type": updated["rule_type"],
            "threshold": float(updated["threshold"] or 0),
            "enabled": updated["enabled"],
            "created_at": updated["created_at"].isoformat()
        }
    }


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(
    alert_id: str,
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        result = await conn.execute(
            "DELETE FROM alerts WHERE id = $1 AND user_id = $2",
            alert_id, user["id"]
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"deleted": True}


# ─── Copy Trade Signals ─────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        signals = await conn.fetch(
            """
            SELECT cts.*, w.address as wallet_address, w.label as wallet_label,
                   w.whale_score
            FROM copy_trade_signals cts
            JOIN wallets w ON w.id = cts.wallet_id
            WHERE w.user_id = $1
            ORDER BY cts.created_at DESC
            LIMIT $2
            """,
            user["id"], limit,
        )
    return {
        "signals": [
            {
                "id": str(s["id"]),
                "token_symbol": s["token_symbol"],
                "action": s["action"],
                "amount_usd": float(s["amount_usd"] or 0),
                "confidence_score": float(s["confidence_score"] or 0),
                "confidence_final": round(
                    0.5 * float(s["confidence_score"] or 0)
                    + 0.5 * float(s["score_at_generation"] or 0), 2
                ),
                "whale_score": float(s["whale_score"] or 0),
                "wallet_address": s["wallet_address"],
                "status": s["status"],
                "wallet_label": s["wallet_label"],
                "created_at": s["created_at"].isoformat(),
                # These columns were added in migration 007; use get() for safety
                # in case the DB schema hasn't been migrated yet.
                "explanation": s.get("explanation") if hasattr(s, "get") else (s["explanation"] if "explanation" in s else None),
                "explanation_stale": bool(s.get("explanation_stale", False)) if hasattr(s, "get") else (s["explanation_stale"] if "explanation_stale" in s else False),
                "score_at_generation": float(s["score_at_generation"] or 0) if "score_at_generation" in s else 0,
            }
            for s in signals
        ]
    }


@app.post("/api/signals/{signal_id}/explain")
async def regenerate_explanation(
    signal_id: str,
    user: dict = Depends(get_current_user),
):
    """Regenerate the explanation text for a signal using current whale score."""
    async with acquire_db() as conn:
        # 1. Fetch signal + verify ownership via wallet join
        signal = await conn.fetchrow(
            """
            SELECT cts.*, w.address as wallet_address, w.label as wallet_label,
                   w.whale_score, w.median_amount_30d, w.execution_rate_30d
            FROM copy_trade_signals cts
            JOIN wallets w ON w.id = cts.wallet_id
            WHERE cts.id = $1 AND w.user_id = $2
            """,
            signal_id, user["id"],
        )

    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    # 2. Fetch current whale score and median from wallets table
    whale_score = float(signal["whale_score"] or 0)
    median_amount_30d = float(signal["median_amount_30d"] or 0)
    execution_rate_30d = float(signal["execution_rate_30d"] or 0)

    # 3. Build signal_data dict for generate_explanation
    tx_type = signal["action"]
    is_receive = tx_type == "receive"
    c_tx = float(signal["confidence_score"] or 0)
    c_final = round(0.5 * c_tx + 0.5 * whale_score, 2)

    signal_data = {
        "action": signal["action"],
        "amount_usd": float(signal["amount_usd"] or 0),
        "token_symbol": signal["token_symbol"],
        "wallet_label": signal["wallet_label"],
        "wallet_address": signal["wallet_address"],
        "is_receive": is_receive,
        "confidence_score": c_tx,
        "confidence_final": c_final,
        "execution_rate_30d": execution_rate_30d,
    }

    from services.signal_generator import generate_explanation
    explanation = generate_explanation(
        signal_data=signal_data,
        whale_score=whale_score,
        median_amount_30d=median_amount_30d,
    )

    # 5. Update the signal row
    async with acquire_db() as conn:
        await conn.execute(
            """
            UPDATE copy_trade_signals
            SET explanation = $2,
                explanation_stale = FALSE,
                score_at_generation = $3
            WHERE id = $1
            """,
            signal_id,
            explanation,
            whale_score,
        )

    return {"explanation": explanation}


@app.post("/api/signals/{signal_id}/mirror")
async def mirror_trade(
    signal_id: str,
    user: dict = Depends(get_current_user),
):
    """Execute a mirror trade via Alpaca paper trading with position sizing."""
    MAX_MIRROR_NOTIONAL = 500.00
    EQUITY_PCT = 0.02
    MIN_NOTIONAL = 1.00

    async with acquire_db() as conn:
        signal = await conn.fetchrow(
            """
            SELECT cts.*, w.address as wallet_address
            FROM copy_trade_signals cts
            JOIN wallets w ON w.id = cts.wallet_id
            WHERE cts.id = $1 AND w.user_id = $2
            """,
            signal_id, user["id"]
        )

    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    # ── Per-user Alpaca credentials ────────────────────────────────────
    alpaca_key: str | None = None
    alpaca_secret: str | None = None

    # Try per-user encrypted keys first
    try:
        from services.crypto import decrypt_secret  # noqa: PLC0415

        async with acquire_db() as conn:
            user_row = await conn.fetchrow(
                """
                SELECT alpaca_api_key_enc,
                       alpaca_api_key_iv,
                       alpaca_secret_key_enc,
                       alpaca_secret_key_iv
                FROM users
                WHERE id = $1
                """,
                user["id"],
            )

        if (
            user_row
            and user_row["alpaca_api_key_enc"] is not None
            and user_row["alpaca_secret_key_enc"] is not None
            and user_row["alpaca_api_key_iv"] is not None
            and user_row["alpaca_secret_key_iv"] is not None
        ):
            alpaca_key = decrypt_secret(user_row["alpaca_api_key_enc"], user_row["alpaca_api_key_iv"])
            alpaca_secret = decrypt_secret(user_row["alpaca_secret_key_enc"], user_row["alpaca_secret_key_iv"])
    except Exception as e:
        logger.warning("Failed to decrypt per-user Alpaca keys for user %s: %s", user["id"], e)

    # Fall back to global env-var keys if ALPACA_ALLOW_SHARED_KEYS is set (dev/demo mode)
    if alpaca_key is None and ALPACA_ALLOW_SHARED_KEYS and ALPACA_API_KEY and ALPACA_SECRET_KEY:
        alpaca_key = ALPACA_API_KEY
        alpaca_secret = ALPACA_SECRET_KEY
        logger.info("Using shared Alpaca keys (ALPACA_ALLOW_SHARED_KEYS=true) for mirror trade")

    if alpaca_key is None or alpaca_secret is None:
        raise HTTPException(
            status_code=402,
            detail="Connect your Alpaca account first via Settings",
        )

    # ── Position sizing (Finding: hardcoded qty=1, no sizing) ────────────
    alpaca_order_id = None
    symbol = signal["token_symbol"]
    signal_amount_usd = float(signal["amount_usd"] or 0)
    try:
        from services.tx_fetcher import _get_client
        client = await _get_client()

        # Fetch portfolio equity
        equity = 0.0
        acct_resp = await client.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers={
                "APCA-API-KEY-ID": alpaca_key,
                "APCA-API-SECRET-KEY": alpaca_secret,
            },
            timeout=15,
        )
        acct_resp.raise_for_status()
        acct_data = acct_resp.json()
        equity = float(acct_data.get("equity", 0))

        if equity <= 0:
            logger.warning("Alpaca equity is 0 or missing, falling back to qty=1")
            notional_usd = 0.0  # triggers qty=1 fallback
        else:
            notional_usd = min(equity * EQUITY_PCT, signal_amount_usd, MAX_MIRROR_NOTIONAL)

        # Normalize 'receive' to 'buy' for Alpaca — both mean "acquiring asset"
        # Defined here (before the if/else) so both branches can use it.
        trade_side = "buy" if signal["action"] in ("buy", "receive") else "sell"

        if notional_usd >= MIN_NOTIONAL:
            # Check if asset supports fractional shares
            use_fractional = True
            try:
                asset_resp = await client.get(
                    f"{ALPACA_BASE_URL}/v2/assets/{symbol}",
                    headers={
                        "APCA-API-KEY-ID": alpaca_key,
                        "APCA-API-SECRET-KEY": alpaca_secret,
                    },
                    timeout=10,
                )
                if asset_resp.status_code == 200:
                    asset_data = asset_resp.json()
                    use_fractional = asset_data.get("fractionable", True)
            except Exception as e:
                logger.warning("Could not check fractionable for %s: %s — assuming fractional", symbol, e)

            order_payload = {
                "symbol": symbol,
                "side": trade_side,
                "type": "market",
                "time_in_force": "day",
            }
            if use_fractional:
                order_payload["notional"] = f"{notional_usd:.2f}"
                logger.info("Mirror trade: symbol=%s notional=%.2f (equity=%.2f, signal_usd=%.2f)",
                           symbol, notional_usd, equity, signal_amount_usd)
            else:
                # Non-fallback: compute qty from price
                try:
                    price_resp = await client.get(
                        f"{ALPACA_BASE_URL}/v2/stocks/{symbol}/trades/latest",
                        headers={
                            "APCA-API-KEY-ID": alpaca_key,
                            "APCA-API-SECRET-KEY": alpaca_secret,
                        },
                        timeout=10,
                    )
                    current_price = float(price_resp.json().get("trade", {}).get("p", 0))
                except Exception:
                    current_price = 0
                if current_price > 0:
                    qty = max(1, int(notional_usd / current_price))
                    order_payload["qty"] = str(qty)
                else:
                    order_payload["qty"] = "1"

            response = await client.post(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers={
                    "APCA-API-KEY-ID": alpaca_key,
                    "APCA-API-SECRET-KEY": alpaca_secret,
                },
                json=order_payload,
                timeout=30,
            )
            response.raise_for_status()
            order_data = response.json()
            alpaca_order_id = order_data.get("id")
        else:
            # notional too small, use qty=1 as before but log
            logger.info("Notional (%.2f) below MIN_NOTIONAL (%.2f), using qty=1 fallback",
                       notional_usd, MIN_NOTIONAL)
            response = await client.post(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers={
                    "APCA-API-KEY-ID": alpaca_key,
                    "APCA-API-SECRET-KEY": alpaca_secret,
                },
                json={
                    "symbol": symbol,
                    "qty": "1",
                    "side": trade_side,
                    "type": "market",
                    "time_in_force": "day",
                },
                timeout=30,
            )
            response.raise_for_status()
            order_data = response.json()
            alpaca_order_id = order_data.get("id")
    except httpx.HTTPStatusError as e:
        logger.error("Alpaca HTTPStatusError for signal %s: %s", signal_id, e.response.text[:200])
        async with acquire_db() as conn:
            await conn.execute(
                """
                UPDATE copy_trade_signals
                SET status = 'failed'
                WHERE id = $1
                """,
                signal_id
            )
        raise HTTPException(
            status_code=502,
            detail=f"Alpaca order failed: {e.response.text[:200]}"
        )
    except Exception as e:
        # Broad exception handler for non-JSON errors, network failures, etc.
        logger.error("Mirror trade unexpected error for signal %s: %s", signal_id, str(e))
        async with acquire_db() as conn:
            await conn.execute(
                """
                UPDATE copy_trade_signals
                SET status = 'failed'
                WHERE id = $1
                """,
                signal_id
            )
        raise HTTPException(status_code=502, detail="Mirror trade failed due to an internal error")

    async with acquire_db() as conn:
        await conn.execute(
            """
            UPDATE copy_trade_signals
            SET status = 'executed', executed_at = NOW()
            WHERE id = $1
            """,
            signal_id
        )

    return {
        "status": "executed",
        "order_id": alpaca_order_id,
        "signal_id": signal_id
    }


# ─── WebSocket Endpoint ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    """
    Authenticated WebSocket endpoint for real-time notifications.
    Clients connect with a valid JWT as a query parameter.
    Events: signal.created, alert.fired
    """
    # Authenticate before accepting the connection
    try:
        payload = verify_jwt(token)
    except HTTPException:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = payload.get("uid")
    if not user_id:
        await websocket.close(code=4001, reason="Token missing uid")
        return

    from services.websocket_manager import websocket_manager

    await websocket_manager.connect(websocket, user_id)
    try:
        while True:
            # Keep the connection alive; respond to client pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket, user_id)


# ─── Whale Sentiment Engine ─────────────────────────────────────────

@app.get("/api/whale-sentiment")
async def get_whale_sentiment(user: dict = Depends(get_current_user)):
    """
    Aggregate the last 50 transactions from all tracked whale wallets
    (is_whale == True) and compute an inflow/outflow sentiment ratio.

    Returns:
        sentiment_score: 0.0 (all outflow) → 1.0 (all inflow), 0.5 = neutral
        classification:  human-readable string
        inflow_usd:      total USD value of 'receive' txns
        outflow_usd:     total USD value of 'send' txns
        tx_count:        number of transactions analysed
    """
    async with acquire_db() as conn:
        rows = await conn.fetch(
            """
            SELECT t.type, t.usd_value
            FROM transactions t
            JOIN wallets w ON w.id = t.wallet_id
            WHERE w.user_id = $1
              AND w.is_whale = TRUE
            ORDER BY t.timestamp DESC
            LIMIT 50
            """,
            user["id"],
        )

    # Cold-start safety: no whale transactions yet → neutral
    if not rows:
        return {
            "sentiment_score": 0.5,
            "classification": "Neutral",
            "inflow_usd": 0.0,
            "outflow_usd": 0.0,
            "tx_count": 0,
        }

    inflow_usd = sum(
        float(r["usd_value"] or 0) for r in rows if r["type"] == "receive"
    )
    outflow_usd = sum(
        float(r["usd_value"] or 0) for r in rows if r["type"] == "send"
    )
    total = inflow_usd + outflow_usd

    # Avoid divide-by-zero (shouldn't happen given the check above, but defensive)
    if total <= 0:
        sentiment_score = 0.5
    else:
        sentiment_score = round(inflow_usd / total, 4)

    if sentiment_score >= 0.65:
        classification = "Accumulating / Bullish"
    elif sentiment_score >= 0.45:
        classification = "Neutral"
    else:
        classification = "Distribution / Bearish"

    return {
        "sentiment_score": sentiment_score,
        "classification": classification,
        "inflow_usd": round(inflow_usd, 2),
        "outflow_usd": round(outflow_usd, 2),
        "tx_count": len(rows),
    }


class TaskCreateRequest(BaseModel):
    task_type: str = Field(default="unknown", max_length=50)
    payload: dict = Field(default_factory=dict)


class TaskCompleteRequest(BaseModel):
    result: dict = Field(default_factory=dict)
    critique: dict = Field(default_factory=dict)


class TaskFailRequest(BaseModel):
    error: str = "unknown"


# ─── Task Queue Shared Secret ────────────────────────────────────────
# Simple shared-secret auth for task_queue endpoints.
# The cron job and the ChainWatch app are in the same Zeabur project network,
# so this provides defense-in-depth without JWT overhead.
_CRON_SECRET = os.environ.get("CRON_SECRET", "")


async def _require_cron_secret(authorization: str = Header("")):
    """Verify the CRON_SECRET header matches.

    Fail-closed: if CRON_SECRET is not configured, deny access.
    This prevents accidentally exposing task-queue endpoints when the
    environment variable is missing (defense-in-depth).
    """
    if not _CRON_SECRET:
        logger.warning(
            "CRON_SECRET not set — task-queue endpoint accessed without secret. "
            "Denying by default (fail-closed)."
        )
        raise HTTPException(
            status_code=503,
            detail="Task queue auth not configured on server",
        )
    if authorization != f"Bearer {_CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Invalid cron secret")


# ─── Task Queue Endpoints (for Hermes cron job) ─────────────────────

@app.get("/api/task-queue/next", dependencies=[Depends(_require_cron_secret)])
async def get_next_task():
    """
    Fetch the next pending task for the cron agent.
    Atomically marks the task as 'running' to prevent double-processing.
    No authentication required — called from within the Zeabur network only.
    """
    async with acquire_db() as conn:
        row = await conn.fetchrow(
            """
            UPDATE task_queue
            SET status = 'running', updated_at = NOW()
            WHERE id = (
                SELECT id FROM task_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, task_type, payload, status, created_at
            """
        )
    if not row:
        return {"task": None}
    return {
        "task": {
            "id": str(row["id"]),
            "task_type": row["task_type"],
            "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            "status": row["status"],
            "created_at": row["created_at"].isoformat(),
        }
    }


@app.post("/api/task-queue/{task_id}/complete", dependencies=[Depends(_require_cron_secret)])
async def complete_task(task_id: str, body: TaskCompleteRequest):
    """
    Mark a task as done with its result and critique.
    Returns {"ok": true, "updated": true/false} indicating whether the task was found.
    """
    async with acquire_db() as conn:
        result = await conn.execute(
            """
            UPDATE task_queue
            SET status = 'done',
                result = $2,
                critique = $3,
                updated_at = NOW()
            WHERE id = $1 AND status = 'running'
            """,
            task_id,
            json.dumps(body.result),
            json.dumps(body.critique),
        )
    # result is "UPDATE 0" or "UPDATE 1"
    updated = result != "UPDATE 0"
    if not updated:
        logger.warning(f"complete_task: task {task_id} not found or not in 'running' status")
    return {"ok": True, "updated": updated}


@app.post("/api/task-queue/{task_id}/fail", dependencies=[Depends(_require_cron_secret)])
async def fail_task(task_id: str, body: TaskFailRequest):
    """Mark a task as failed with a reason."""
    async with acquire_db() as conn:
        result = await conn.execute(
            """
            UPDATE task_queue
            SET status = 'failed',
                result = $2,
                updated_at = NOW()
            WHERE id = $1 AND status = 'running'
            """,
            task_id,
            json.dumps({"error": body.error}),
        )
    updated = result != "UPDATE 0"
    if not updated:
        logger.warning(f"fail_task: task {task_id} not found or not in 'running' status")
    return {"ok": True, "updated": updated}


@app.post("/api/task-queue", dependencies=[Depends(_require_cron_secret)])
async def create_task(body: TaskCreateRequest):
    """
    Insert a new task into the queue.
    """
    async with acquire_db() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO task_queue (task_type, payload, status)
            VALUES ($1, $2, 'pending')
            RETURNING id, created_at
            """,
            body.task_type, json.dumps(body.payload),
        )
    return {
        "id": str(row["id"]),
        "task_type": body.task_type,
        "status": "pending",
        "created_at": row["created_at"].isoformat(),
    }


# ─── Health Check (comprehensive) ───────────────────────────────────

@app.get("/api/health")
async def health_check():
    """
    Comprehensive health check endpoint.
    Returns status of all subsystems: DB, monitor, price cache, API keys, WS connections.
    """
    import time as _time

    report = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "version": "1.0.0",
        "subsystems": {},
    }

    # ── DB connectivity ──
    db_ok = False
    db_latency_ms = None
    wallet_count = 0
    signal_count = 0
    table_count = 0
    t0 = _time.monotonic()
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                db_ok = True
                wallet_count = await conn.fetchval("SELECT COUNT(*) FROM wallets")
                signal_count = await conn.fetchval("SELECT COUNT(*) FROM copy_trade_signals")
                table_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"
                )
        except Exception as e:
            db_ok = False
    db_latency_ms = round((_time.monotonic() - t0) * 1000, 1)
    report["subsystems"]["db"] = {
        "ok": db_ok,
        "latency_ms": db_latency_ms,
        "wallet_count": wallet_count,
        "signal_count": signal_count,
        "table_count": table_count,
    }

    # ── Monitor worker ──
    try:
        from services.monitor import is_monitor_alive, get_cycle_stats
        monitor_alive = is_monitor_alive()
        cycle_stats = await get_cycle_stats()
    except Exception:
        monitor_alive = False
        cycle_stats = {}
    report["subsystems"]["monitor"] = {
        "alive": monitor_alive,
        "cycle_stats": cycle_stats,
    }

    # ── Price cache freshness ──
    try:
        from services.monitor import _price_cache
        cache_age = _time.time() - _price_cache.get("timestamp", 0)
        report["subsystems"]["price_cache"] = {
            "fresh": cache_age < 120,
            "age_seconds": round(cache_age, 1),
            "eth": _price_cache.get("ETH", 0),
            "sol": _price_cache.get("SOL", 0),
            "btc": _price_cache.get("BTC", 0),
        }
    except Exception as e:
        report["subsystems"]["price_cache"] = {"ok": False, "error": str(e)}

    # ── Dashboard price cache (CoinGecko cross-rates) ──
    try:
        cache_age = _time.time() - _dashboard_price_cache.get("timestamp", 0)
        report["subsystems"]["dashboard_prices"] = {
            "fresh": cache_age < 300,
            "age_seconds": round(cache_age, 1),
            "usd_hkd": _dashboard_price_cache.get("USDHKD", 0),
            "usd_btc": _dashboard_price_cache.get("USDBTC", 0),
        }
    except Exception as e:
        report["subsystems"]["dashboard_prices"] = {"ok": False, "error": str(e)}

    # ── API key configuration (presence only, not values) ──
    report["subsystems"]["api_keys"] = {
        "etherscan": len(ETHERSCAN_API_KEY) > 0,
        "solscan": len(SOLSCAN_API_KEY) > 0,
        "blockchair": len(BLOCKCHAIR_API_KEY) > 0,
        "telegram": len(TELEGRAM_BOT_TOKEN) > 0 and len(TELEGRAM_CHAT_ID) > 0,
        "alpaca": len(ALPACA_API_KEY) > 0 or ALPACA_ALLOW_SHARED_KEYS,
    }

    # ── WebSocket connections ──
    try:
        from services.websocket_manager import websocket_manager
        ws_count = sum(len(conns) for conns in websocket_manager._connections.values())
    except Exception:
        ws_count = 0
    report["subsystems"]["websocket"] = {"active_connections": ws_count}

    # ── overall status ──
    critical_ok = db_ok and monitor_alive
    report["status"] = "healthy" if critical_ok else "degraded"

    status_code = 200 if critical_ok else 503
    return JSONResponse(content=report, status_code=status_code)


@app.get("/api/health/diagnostic")
async def health_diagnostic():
    """
    Startup diagnostic endpoint — helps debug connection and configuration issues.
    Reports: DB hostname resolution, DATABASE_URL parse, env var presence,
    monitor state, price cache state, and migration log summary.
    Safe to expose: does not leak secrets (masks API keys, shows only presence).
    """
    import socket as _socket
    import time as _time

    report = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": {},
    }

    # ── 1. DATABASE_URL parse & hostname resolution ──
    db_url = os.environ.get("DATABASE_URL", "")
    db_host = None
    db_port = None
    db_name = None
    db_resolution = None
    if db_url:
        try:
            # Parse: postgresql://user:pass@host:port/dbname
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            db_host = parsed.hostname
            db_port = parsed.port
            db_name = parsed.path.lstrip("/") if parsed.path else None
            # Attempt DNS resolution
            if db_host:
                try:
                    resolved = _socket.getaddrinfo(db_host, None, _socket.AF_INET)
                    db_resolution = resolved[0][4][0] if resolved else None
                except Exception as e:
                    db_resolution = f"FAILED: {e}"
        except Exception as e:
            report["checks"]["db_url"] = {"error": f"Parse failed: {e}"}
    report["checks"]["db_url"] = {
        "configured": bool(db_url),
        "host": db_host,
        "port": db_port,
        "database": db_name,
        "resolved_ip": db_resolution,
        "reachable": None,  # filled below
    }

    # ── 2. DB connectivity test (with timeout) ──
    db_reachable = False
    db_error = None
    if db_pool is not None:
        try:
            t0 = _time.monotonic()
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_reachable = True
            report["checks"]["db_url"]["latency_ms"] = round((_time.monotonic() - t0) * 1000, 1)
        except Exception as e:
            db_error = str(e)
    else:
        db_error = "db_pool is None — startup handler failed to create pool"
    report["checks"]["db_url"]["reachable"] = db_reachable
    if db_error:
        report["checks"]["db_url"]["error"] = db_error

    # ── 3. TCP reachability to DB host:port (independent of asyncpg) ──
    if db_host and db_port:
        try:
            t0 = _time.monotonic()
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((db_host, db_port))
            sock.close()
            report["checks"]["db_url"]["tcp_reachable"] = result == 0
            report["checks"]["db_url"]["tcp_latency_ms"] = round((_time.monotonic() - t0) * 1000, 1)
        except Exception as e:
            report["checks"]["db_url"]["tcp_reachable"] = False
            report["checks"]["db_url"]["tcp_error"] = str(e)

    # ── 4. Environment variable presence (not values) ──
    report["checks"]["env"] = {
        "DATABASE_URL": bool(os.environ.get("DATABASE_URL")),
        "CRON_SECRET": bool(os.environ.get("CRON_SECRET")),
        "CHAINWATCH_MASTER_KEY": bool(os.environ.get("CHAINWATCH_MASTER_KEY")),
        "ETHERSCAN_API_KEY": bool(os.environ.get("ETHERSCAN_API_KEY")),
        "SOLSCAN_API_KEY": bool(os.environ.get("SOLSCAN_API_KEY")),
        "BLOCKCHAIR_API_KEY": bool(os.environ.get("BLOCKCHAIR_API_KEY")),
        "TELEGRAM_BOT_TOKEN": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "ALPACA_API_KEY": bool(os.environ.get("ALPACA_API_KEY")),
        "ALPACA_API_SECRET": bool(os.environ.get("ALPACA_API_SECRET")),
        "JWT_SECRET": bool(os.environ.get("JWT_SECRET")),
        "ENV": os.environ.get("ENV", "not set"),
        "PORT": os.environ.get("PORT", "not set"),
    }

    # ── 5. Monitor state ──
    try:
        from services.monitor import is_monitor_alive, _price_cache, POLL_INTERVAL, MAX_CONSECUTIVE_ERRORS
        monitor_alive = is_monitor_alive()
        cache_age = _time.time() - _price_cache.get("timestamp", 0)
        report["checks"]["monitor"] = {
            "alive": monitor_alive,
            "poll_interval_s": POLL_INTERVAL,
            "max_consecutive_errors": MAX_CONSECUTIVE_ERRORS,
            "price_cache_age_s": round(cache_age, 1),
            "price_cache_fresh": cache_age < 120,
            "price_cache_eth": _price_cache.get("ETH", 0),
            "price_cache_sol": _price_cache.get("SOL", 0),
            "price_cache_btc": _price_cache.get("BTC", 0),
        }
    except Exception as e:
        report["checks"]["monitor"] = {"error": str(e)}

    # ── 6. Migration log summary ──
    if db_pool is not None and db_reachable:
        try:
            async with db_pool.acquire() as conn:
                applied = await conn.fetch(
                    "SELECT file_name, applied_at FROM _migration_log ORDER BY applied_at"
                )
                report["checks"]["migrations"] = {
                    "applied_count": len(applied),
                    "latest": applied[-1]["file_name"] if applied else None,
                    "latest_at": str(applied[-1]["applied_at"]) if applied else None,
                }
        except Exception as e:
            report["checks"]["migrations"] = {"error": str(e)}

    # ── 7. System info ──
    report["checks"]["system"] = {
        "hostname": _socket.gethostname(),
        "python_version": __import__("sys").version.split()[0],
        "cwd": os.getcwd(),
    }

    return JSONResponse(content=report)


@app.get("/api/health/startup-log")
async def health_startup_log():
    """
    Returns the last N lines of captured startup log entries.
    Complements /api/health/diagnostic with historical startup data:
    DB connection attempt, monitor launch, price cache init, etc.
    Safe: contains no secrets, only event names and timestamps.
    """
    return JSONResponse(content={
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "entries": _get_startup_log_entries(),
    })


# ─── Application Metrics ─────────────────────────────────────────────

@app.get("/api/health/metrics")
async def health_metrics():
    """
    Application-level metrics: request counts, error rates, DB query stats,
    and per-endency latency percentiles. Useful for dashboards and alerting.
    Resets on restart (in-memory only — no persistent time-series).
    """
    from services.health_metrics import get_metrics
    return JSONResponse(content=get_metrics())


# ─── Whale Scorer Diagnostic ─────────────────────────────────────────

@app.get("/api/wallets/{wallet_id}/score")
async def get_wallet_score(
    wallet_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Run the whale scorer on-demand for a specific wallet and return the
    full scoring breakdown. Useful for debugging why a wallet has a
    particular whale score without adding debug logging.

    Returns the same dict as whale_scorer.score_whale_wallet() plus
    wallet metadata (address, chain, label, balance).
    """
    # Fetch wallet, global median, and compute score using a single DB connection
    # to avoid acquiring 3 separate pool connections (Pitfall #7: connection efficiency).
    _global_median_30d = 0.0
    from services.whale_scorer import score_whale_wallet
    async with acquire_db() as conn:
        wallet = await conn.fetchrow(
            "SELECT * FROM wallets WHERE id = $1 AND user_id = $2",
            wallet_id, user["id"],
        )
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        try:
            _gm_row = await conn.fetchrow(
                """
                SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cts.amount_usd)
                    AS global_median_30d
                FROM copy_trade_signals cts
                JOIN wallets w ON w.id = cts.wallet_id
                WHERE w.is_whale = TRUE
                  AND cts.created_at >= NOW() - INTERVAL '30 days'
                """
            )
            if _gm_row and _gm_row["global_median_30d"] is not None:
                _global_median_30d = float(_gm_row["global_median_30d"])
        except Exception:
            pass  # whale_scorer will fall back to per-wallet subquery

        score_data = await score_whale_wallet(
            conn, wallet_id, global_median_30d=_global_median_30d,
        )

    return {
        "wallet_id": wallet_id,
        "address": wallet["address"],
        "chain": wallet["chain"],
        "label": wallet["label"],
        "is_whale": wallet["is_whale"],
        "balance_usd": float(wallet["balance_usd"] or 0),
        "balance_native": float(wallet["balance_native"] or 0),
        "score": score_data["score"],
        "score_activity": score_data["score_activity"],
        "score_reliability": score_data["score_reliability"],
        "score_weight": score_data["score_weight"],
        "score_recency": score_data["score_recency"],
        "score_diversity": score_data["score_diversity"],
        "score_signals_used": score_data["score_signals_used"],
        "score_is_coldstart": score_data["score_is_coldstart"],
        "median_amount_30d": score_data["median_amount_30d"],
        "execution_rate_30d": score_data["execution_rate_30d"],
        "db_stored_score": float(wallet.get("whale_score") or 0),
        "db_score_calculated_at": wallet["score_calculated_at"].isoformat() if wallet.get("score_calculated_at") else None,
    }


# ─── Serve Frontend (production) ────────────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Only mount static files if the build directory exists
_static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")
if os.path.isdir(_static_dir):
    _assets_dir = os.path.join(_static_dir, "static")
    if os.path.isdir(_assets_dir):
        app.mount("/static", StaticFiles(directory=_assets_dir), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        return FileResponse(os.path.join(_static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=os.environ.get("ENV", "production") == "development"
    )
