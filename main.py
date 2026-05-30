"""
ChainWatch - Crypto Portfolio Tracker
FastAPI Backend
"""
import os
import uuid
import hashlib
import time
import json
import httpx
import secrets
from datetime import datetime, timedelta
from typing import Optional, List
from decimal import Decimal

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import asyncpg
import jwt

# Configuration
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/chainwatch"
)
JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    secrets.token_hex(32)
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

app = FastAPI(
    title="ChainWatch",
    description="Crypto Portfolio Tracker",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database pool
db_pool: Optional[asyncpg.Pool] = None


@app.on_event("startup")
async def startup():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    except Exception as e:
        import logging
        logging.warning(f"Failed to create DB pool on startup: {e}. API endpoints requiring DB will return 503.")
        db_pool = None


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()


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
    """Get a DB connection handle for direct use. Raises 503 if DB is unavailable."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool.acquire()


def create_jwt(wallet_address: str) -> str:
    payload = {
        "sub": wallet_address,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=7),
        "jti": str(uuid.uuid4())
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    authorization: str = Header(...)
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization[7:]
    payload = verify_jwt(token)
    
    async with acquire_db() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE wallet_address = $1",
            payload["sub"]
        )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


# ─── Pydantic Models ───────────────────────────────────────────────

class WalletConnectRequest(BaseModel):
    wallet_address: str = Field(..., min_length=10, max_length=255)
    signature: str = Field(..., min_length=10)
    message: str = Field(..., min_length=10)


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
    threshold: float = 0.0
    enabled: bool = True


class AlertUpdateRequest(BaseModel):
    threshold: Optional[float] = None
    enabled: Optional[bool] = None


# ─── Auth Endpoints ─────────────────────────────────────────────────

@app.post("/api/auth/challenge")
async def create_challenge(wallet_address: str = Query(...)):
    """Create a signing challenge for WalletConnect auth."""
    nonce = secrets.token_hex(16)
    message = (
        f"ChainWatch Authentication\n\n"
        f"Sign this message to prove ownership of {wallet_address}.\n\n"
        f"Nonce: {nonce}\n"
        f"Timestamp: {int(time.time())}\n"
        f"Domain: chainwatch.app"
    )
    return {
        "message": message,
        "nonce": nonce,
        "wallet_address": wallet_address
    }


@app.post("/api/auth/verify")
async def verify_signature(req: WalletConnectRequest):
    """Verify wallet signature and return JWT."""
    # Verify the signature matches the message and wallet
    # In production, use eth_account or solana verify libraries
    # For now, we accept the signature and create a session
    
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
        
        # Create JWT
        token = create_jwt(req.wallet_address.lower())
        
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
    return {
        "wallet_address": user["wallet_address"],
        "created_at": user["created_at"].isoformat()
    }


# ─── Dashboard Endpoint ─────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(user: dict = Depends(get_current_user)):
    async with acquire_db() as conn:
        # Get all user wallets with balances
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
            user["id"]
        )
        
        # Recent transactions
        recent_txs = await conn.fetch(
            """
            SELECT t.*, w.address as wallet_address, w.chain, w.label
            FROM transactions t
            JOIN wallets w ON w.id = t.wallet_id
            WHERE w.user_id = $1
            ORDER BY t.timestamp DESC
            LIMIT 20
            """,
            user["id"]
        )
        
        # Active alerts
        alerts = await conn.fetch(
            "SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC",
            user["id"]
        )
        
        # Copy trade signals
        signals = await conn.fetch(
            """
            SELECT cts.*, w.address as wallet_address, w.label as wallet_label
            FROM copy_trade_signals cts
            JOIN wallets w ON w.id = cts.wallet_id
            WHERE w.user_id = $1
            ORDER BY cts.created_at DESC
            LIMIT 10
            """,
            user["id"]
        )
    
    # Calculate total portfolio value
    total_value = sum(
        float(w["total_received"] or 0) - float(w["total_sent"] or 0)
        for w in wallets
    )
    
    return {
        "portfolio": {
            "total_value_usd": round(total_value, 2),
            "wallets_tracked": len(wallets),
            "whale_wallets": sum(1 for w in wallets if w["is_whale"]),
            "personal_wallets": sum(1 for w in wallets if w["is_mine"]),
        },
        "wallets": [
            {
                "id": str(w["id"]),
                "address": w["address"],
                "chain": w["chain"],
                "label": w["label"],
                "is_whale": w["is_whale"],
                "is_mine": w["is_mine"],
                "balance_usd": round(
                    float(w["total_received"] or 0) - float(w["total_sent"] or 0), 2
                ),
                "created_at": w["created_at"].isoformat()
            }
            for w in wallets
        ],
        "recent_transactions": [
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
                "wallet_address": t["wallet_address"]
            }
            for t in recent_txs
        ],
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
                "status": s["status"],
                "wallet_label": s["wallet_label"],
                "created_at": s["created_at"].isoformat()
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
            await conn.execute(
                f"UPDATE wallets SET {set_clauses} WHERE id = $1 AND user_id = $2",
                wallet_id, user["id"], *values
            )
        
        updated = await conn.fetchrow(
            "SELECT * FROM wallets WHERE id = $1", wallet_id
        )
    
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


# ─── Whale Suggestions ──────────────────────────────────────────────

@app.get("/api/whale-suggestions")
async def get_whale_suggestions(chain: Optional[str] = Query(None)):
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
    limit: int = Query(50, ge=1, le=200),
    chain: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    async with acquire_db() as conn:
        params = [user["id"], limit]
        chain_filter = ""
        if chain:
            params.append(chain)
            chain_filter = "AND w.chain = $3"
        
        transactions = await conn.fetch(
            f"""
            SELECT t.*, w.address as wallet_address, w.chain, w.label
            FROM transactions t
            JOIN wallets w ON w.id = t.wallet_id
            WHERE w.user_id = $1 {chain_filter}
            ORDER BY t.timestamp DESC
            LIMIT $2
            """,
            *params
        )
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
                "wallet_address": t["wallet_address"]
            }
            for t in transactions
        ]
    }


# ─── Alerts ─────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    async with acquire_db() as conn:
        alerts = await conn.fetch(
            "SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC",
            user["id"]
        )
    return {
        "alerts": [
            {
                "id": str(a["id"]),
                "rule_type": a["rule_type"],
                "threshold": float(a["threshold"] or 0),
                "enabled": a["enabled"],
                "created_at": a["created_at"].isoformat()
            }
            for a in alerts
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
            INSERT INTO alerts (user_id, rule_type, threshold, enabled)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user["id"],
            req.rule_type,
            req.threshold,
            req.enabled
        )
    return {
        "alert": {
            "id": str(alert["id"]),
            "rule_type": alert["rule_type"],
            "threshold": float(alert["threshold"] or 0),
            "enabled": alert["enabled"],
            "created_at": alert["created_at"].isoformat()
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
            await conn.execute(
                f"UPDATE alerts SET {set_clauses} WHERE id = $1 AND user_id = $2",
                alert_id, user["id"], *values
            )
        
        updated = await conn.fetchrow(
            "SELECT * FROM alerts WHERE id = $1", alert_id
        )
    
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
            SELECT cts.*, w.address as wallet_address, w.label as wallet_label
            FROM copy_trade_signals cts
            JOIN wallets w ON w.id = cts.wallet_id
            WHERE w.user_id = $1
            ORDER BY cts.created_at DESC
            LIMIT $2
            """,
            user["id"], limit
        )
    return {
        "signals": [
            {
                "id": str(s["id"]),
                "token_symbol": s["token_symbol"],
                "action": s["action"],
                "amount_usd": float(s["amount_usd"] or 0),
                "confidence_score": float(s["confidence_score"] or 0),
                "status": s["status"],
                "wallet_label": s["wallet_label"],
                "created_at": s["created_at"].isoformat()
            }
            for s in signals
        ]
    }


@app.post("/api/signals/{signal_id}/mirror")
async def mirror_trade(
    signal_id: str,
    user: dict = Depends(get_current_user)
):
    """Execute a mirror trade via Alpaca paper trading."""
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
    
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Alpaca trading not configured"
        )
    
    # Execute paper trade
    alpaca_order_id = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers={
                    "APCA-API-KEY-ID": ALPACA_API_KEY,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
                },
                json={
                    "symbol": signal["token_symbol"],
                    "qty": "1",
                    "side": "buy" if signal["action"] == "buy" else "sell",
                    "type": "market",
                    "time_in_force": "day",
                },
                timeout=30
            )
            response.raise_for_status()
            order_data = response.json()
            alpaca_order_id = order_data.get("id")
    except httpx.HTTPStatusError as e:
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
            detail=f"Alpaca order failed: {e.response.text}"
        )

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


# ─── Health Check ───────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    try:
        if db_pool is None:
            return JSONResponse({"status": "starting", "database": "not_initialized"}, status_code=503)
        async with acquire_db() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse({"status": "unhealthy", "error": str(e)}, status_code=503)


# ─── Serve Frontend (production) ────────────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Only mount static files if the build directory exists
import os
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
