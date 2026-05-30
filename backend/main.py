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
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import asyncpg
import jwt

# Configuration
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
) or os.environ.get("POSTGRES_CONNECTION_STRING")
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

# In-memory stores for when DB is not available
_users: dict[str, dict] = {}
_wallets: list[dict] = []
_alerts: list[dict] = []
_fake_id_counter = 0


def _fake_id() -> str:
    global _fake_id_counter
    _fake_id_counter += 1
    return str(_fake_id_counter)


_WHALE_SUGGESTIONS: list[dict] = [
    {"chain": "eth", "address": "0x28C6c06298d514Db089934071355E5743bf21d60", "label": "Binance Hot Wallet", "source": "public"},
    {"chain": "eth", "address": "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549", "label": "Binance Cold Wallet", "source": "public"},
    {"chain": "eth", "address": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8", "label": "Anchorage Digital", "source": "public"},
    {"chain": "eth", "address": "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F", "label": "Crypto.com", "source": "public"},
    {"chain": "eth", "address": "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d", "label": "Kraken", "source": "public"},
    {"chain": "sol", "address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "label": "Raydium Authority", "source": "public"},
    {"chain": "sol", "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "label": "Binance SOL", "source": "public"},
    {"chain": "sol", "address": "HXVJVK5HtoCVLfALx9RPN2rbX7gKBUDRQM7XhUqppump", "label": "Pump.fun Authority", "source": "public"},
    {"chain": "sol", "address": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "label": "Raydium AMM", "source": "public"},
    {"chain": "sol", "address": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "label": "Jupiter Aggregator", "source": "public"},
    {"chain": "btc", "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", "label": "Binance BTC", "source": "public"},
    {"chain": "btc", "address": "bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt", "label": "Bitfinex Cold", "source": "public"},
    {"chain": "btc", "address": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "label": "Binance Cold BTC", "source": "public"},
]


@app.on_event("startup")
async def startup():
    global db_pool
    db_url = DATABASE_URL
    if not db_url:
        import logging
        logging.warning("No DATABASE_URL set. Using in-memory store. Data will not persist across restarts.")
        db_pool = None
        return
    try:
        db_pool = await asyncpg.create_pool(
            db_url,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
        async with db_pool.acquire() as conn:
            migration_path = os.path.join(os.path.dirname(__file__), "migrations", "001_initial_schema.sql")
            migration_sql = open(migration_path).read()
            await conn.execute(migration_sql)
    except Exception as e:
        import logging
        logging.warning(f"Failed to connect to DB: {e}. Using in-memory store.")
        db_pool = None


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()


# ─── Helpers ────────────────────────────────────────────────────────

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

    if db_pool is not None:
        async with db_pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE wallet_address = $1",
                payload["sub"]
            )
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return dict(user)
    else:
        user = _users.get(payload["sub"].lower())
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["id"] = user.get("id", user["wallet_address"])
        return user


def _get_db_conn():
    """Return a context manager that yields a DB connection or fake in-memory conn."""
    if db_pool is not None:
        return db_pool.acquire()
    return _FakePool()


class _FakeConn:
    async def fetchrow(self, query: str, *args):
        ql = query.lower()
        now = datetime.utcnow()
        if "insert into users" in ql:
            addr = args[0] if args else ""
            return {"wallet_address": addr, "id": addr, "created_at": now,
                    "session_token": None, "session_expires_at": None}
        if "select * from users where wallet_address" in ql:
            addr = args[0] if args else None
            return _users.get(addr) if addr else None
        if "insert into wallets" in ql:
            w = {"id": _fake_id(), "user_id": args[0], "address": args[1],
                 "chain": args[2] if len(args) > 2 else "eth",
                 "label": args[3] if len(args) > 3 else "",
                 "is_whale": args[4] if len(args) > 4 else False,
                 "is_mine": args[5] if len(args) > 5 else False,
                 "created_at": now}
            _wallets.append(w)
            return w
        if "select * from wallets where id" in ql and "user_id" in ql:
            wid = args[0]
            uid = args[1] if len(args) > 1 else None
            for w in _wallets:
                if str(w["id"]) == str(wid) and str(w.get("user_id")) == str(uid):
                    return w
            return None
        if "insert into alerts" in ql:
            a = {"id": _fake_id(), "user_id": args[0],
                 "rule_type": args[1] if len(args) > 1 else "",
                 "threshold": float(args[2]) if len(args) > 2 else 0.0,
                 "enabled": args[3] if len(args) > 3 else True,
                 "created_at": now}
            _alerts.append(a)
            return a
        if "update wallets set" in ql:
            wid = args[0]
            for w in _wallets:
                if str(w["id"]) == str(wid):
                    if len(args) > 2 and args[1]: w["label"] = args[1]
                    if len(args) > 3 and args[2] is not None: w["is_mine"] = args[2]
            return None
        if "update alerts set" in ql:
            aid = args[0]
            for a in _alerts:
                if str(a["id"]) == str(aid):
                    if len(args) > 2 and args[1] is not None: a["threshold"] = float(args[1])
                    if len(args) > 3 and args[2] is not None: a["enabled"] = args[2]
            return None
        return None

    async def fetch(self, query: str, *args):
        ql = query.lower()
        if "select * from users where" in ql:
            addr = args[0] if args else None
            u = _users.get(addr) if addr else None
            return [u] if u else []
        if "from wallets where user_id" in ql:
            uid = args[0] if args else None
            results = [w for w in _wallets if str(w.get("user_id")) == str(uid)]
            results.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
            return results
        if "from whale_suggestions" in ql:
            if args:
                chain = args[0]
                return [s for s in _WHALE_SUGGESTIONS if s["chain"] == chain][:5]
            return _WHALE_SUGGESTIONS[:15]
        if "from alerts where user_id" in ql:
            uid = args[0] if args else None
            return [a for a in _alerts if str(a.get("user_id")) == str(uid)]
        if "from transactions" in ql or "from copy_trade" in ql:
            return []
        if "select w.*" in ql or ("group by" in ql and "wallets" in ql):
            uid = args[0] if args else None
            results = [w for w in _wallets if str(w.get("user_id")) == str(uid)]
            return results
        return []

    async def execute(self, query: str, *args):
        ql = query.lower()
        if "update users set session_token" in ql:
            token_val = args[0] if args else None
            addr = args[2] if len(args) > 2 else None
            if addr and token_val and isinstance(addr, str):
                addr = addr.lower()
                if addr in _users:
                    _users[addr]["session_token"] = token_val
            return "UPDATE 1"
        if "delete from wallets" in ql:
            wid = args[0] if args else None
            global _wallets
            _wallets = [w for w in _wallets if str(w.get("id")) != str(wid)]
            return "DELETE 1"
        if "delete from alerts" in ql:
            aid = args[0] if args else None
            global _alerts
            _alerts = [a for a in _alerts if str(a.get("id")) != str(aid)]
            return "DELETE 1"
        if "update copy_trade" in ql:
            return "UPDATE 1"
        if "insert into" in ql:
            return "INSERT 0 1"
        if "update" in ql:
            return "UPDATE 1"
        return "SELECT 1"

    async def fetchval(self, query, *args):
        return 1


class _FakePool:
    async def __aenter__(self):
        return _FakeConn()
    async def __aexit__(self, *args):
        pass


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
@app.get("/api/auth/challenge")
async def create_challenge(wallet_address: str = Query(...)):
    nonce = secrets.token_hex(16)
    message = (
        f"ChainWatch Authentication\n\n"
        f"Sign this message to prove ownership of {wallet_address}.\n\n"
        f"Nonce: {nonce}\n"
        f"Timestamp: {int(time.time())}\n"
        f"Domain: chainwatch.app"
    )
    return {"message": message, "nonce": nonce, "wallet_address": wallet_address}


@app.post("/api/auth/verify")
async def verify_signature(req: WalletConnectRequest):
    addr = req.wallet_address.lower()
    token = create_jwt(addr)

    if db_pool is not None:
        async with db_pool.acquire() as conn:
            user = await conn.fetchrow(
                "INSERT INTO users (wallet_address) VALUES ($1) "
                "ON CONFLICT (wallet_address) DO UPDATE SET wallet_address = $1 "
                "RETURNING *",
                addr
            )
            await conn.execute(
                "UPDATE users SET session_token = $1, session_expires_at = $2 WHERE wallet_address = $3",
                token, datetime.utcnow() + timedelta(days=7), addr
            )
        user_dict = {"wallet_address": user["wallet_address"], "created_at": user["created_at"].isoformat()}
    else:
        now = datetime.utcnow()
        if addr not in _users:
            _users[addr] = {"wallet_address": addr, "id": addr, "created_at": now,
                            "session_token": token, "session_expires_at": now + timedelta(days=7)}
        else:
            _users[addr]["session_token"] = token
            _users[addr]["session_expires_at"] = now + timedelta(days=7)
            _users[addr]["id"] = addr
        u = _users[addr]
        user_dict = {"wallet_address": addr,
                     "created_at": u["created_at"].isoformat() if isinstance(u["created_at"], datetime) else u["created_at"]}

    return {"token": token, "user": user_dict}


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    created_at = user.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {"wallet_address": user["wallet_address"], "created_at": created_at}


# ─── Dashboard ──────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        wallets_raw = await conn.fetch("SELECT * FROM wallets WHERE user_id = $1 ORDER BY created_at DESC", uid)
        alerts_raw = await conn.fetch("SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC", uid)

    wallets_out = []
    total_value = 0.0
    for w in wallets_raw:
        bal = float(w.get("balance_usd") or 0)
        total_value += bal
        wallets_out.append({
            "id": str(w["id"]), "address": w["address"], "chain": w["chain"],
            "label": w.get("label", ""), "is_whale": w.get("is_whale", False),
            "is_mine": w.get("is_mine", False), "balance_usd": round(bal, 2),
            "created_at": w["created_at"].isoformat() if isinstance(w.get("created_at"), datetime) else str(w.get("created_at","")),
        })

    alerts_out = []
    for a in alerts_raw:
        alerts_out.append({
            "id": str(a["id"]), "rule_type": a.get("rule_type", ""),
            "threshold": float(a.get("threshold") or 0), "enabled": a.get("enabled", True),
            "created_at": a["created_at"].isoformat() if isinstance(a.get("created_at"), datetime) else str(a.get("created_at","")),
        })

    return {
        "portfolio": {
            "total_value_usd": round(total_value, 2), "wallets_tracked": len(wallets_out),
            "whale_wallets": sum(1 for w in wallets_out if w["is_whale"]),
            "personal_wallets": sum(1 for w in wallets_out if w["is_mine"]),
        },
        "wallets": wallets_out,
        "recent_transactions": [],
        "alerts": alerts_out,
        "copy_trade_signals": [],
    }


# ─── Wallet Endpoints ───────────────────────────────────────────────

@app.get("/api/wallets")
async def list_wallets(user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        rows = await conn.fetch("SELECT * FROM wallets WHERE user_id = $1 ORDER BY created_at DESC", uid)
    return {"wallets": [{
        "id": str(w["id"]), "address": w["address"], "chain": w["chain"],
        "label": w.get("label", ""), "is_whale": w.get("is_whale", False),
        "is_mine": w.get("is_mine", False),
        "created_at": w["created_at"].isoformat() if isinstance(w.get("created_at"), datetime) else str(w.get("created_at","")),
    } for w in rows]}


@app.post("/api/wallets")
async def add_wallet(req: WalletAddRequest, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO wallets (user_id, address, chain, label, is_whale, is_mine) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
            uid, req.address, req.chain, req.label, req.is_whale, req.is_mine
        )
    return {"wallet": {
        "id": str(row["id"]), "address": row["address"], "chain": row["chain"],
        "label": row.get("label", ""), "is_whale": row.get("is_whale", False),
        "is_mine": row.get("is_mine", False),
        "created_at": row["created_at"].isoformat() if isinstance(row.get("created_at"), datetime) else str(row.get("created_at","")),
    }}


@app.put("/api/wallets/{wallet_id}")
async def update_wallet(wallet_id: str, req: WalletUpdateRequest, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        existing = await conn.fetchrow("SELECT * FROM wallets WHERE id = $1 AND user_id = $2", wallet_id, uid)
        if not existing:
            raise HTTPException(status_code=404, detail="Wallet not found")
        updates = {}
        if req.label is not None: updates["label"] = req.label
        if req.is_whale is not None: updates["is_whale"] = req.is_whale
        if req.is_mine is not None: updates["is_mine"] = req.is_mine
        if updates:
            set_clause = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
            await conn.execute(f"UPDATE wallets SET {set_clause} WHERE id = $1 AND user_id = $2",
                               wallet_id, uid, *updates.values())
        row = await conn.fetchrow("SELECT * FROM wallets WHERE id = $1", wallet_id)
    return {"wallet": {
        "id": str(row["id"]), "address": row["address"], "chain": row["chain"],
        "label": row.get("label", ""), "is_whale": row.get("is_whale", False),
        "is_mine": row.get("is_mine", False),
        "created_at": row["created_at"].isoformat() if isinstance(row.get("created_at"), datetime) else str(row.get("created_at","")),
    }}


@app.delete("/api/wallets/{wallet_id}")
async def delete_wallet(wallet_id: str, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        result = await conn.execute("DELETE FROM wallets WHERE id = $1 AND user_id = $2", wallet_id, uid)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"deleted": True}


# ─── Whale Suggestions ──────────────────────────────────────────────

@app.get("/api/whale-suggestions")
async def get_whale_suggestions(chain: Optional[str] = Query(None)):
    async with _get_db_conn() as conn:
        if chain:
            rows = await conn.fetch("SELECT * FROM whale_suggestions WHERE chain = $1 ORDER BY added_at DESC LIMIT 5", chain)
        else:
            rows = await conn.fetch("SELECT * FROM whale_suggestions ORDER BY chain, added_at DESC LIMIT 15")
    return {"suggestions": [dict(r) for r in rows]}


# ─── Activity / Transactions ────────────────────────────────────────

@app.get("/api/activity")
async def get_activity(limit: int = Query(50, ge=1, le=200), chain: Optional[str] = Query(None),
                       user: dict = Depends(get_current_user)):
    return {"transactions": []}


# ─── Alerts ─────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        rows = await conn.fetch("SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC", uid)
    return {"alerts": [{
        "id": str(a["id"]), "rule_type": a.get("rule_type", ""),
        "threshold": float(a.get("threshold") or 0), "enabled": a.get("enabled", True),
        "created_at": a["created_at"].isoformat() if isinstance(a.get("created_at"), datetime) else str(a.get("created_at","")),
    } for a in rows]}


@app.post("/api/alerts")
async def create_alert(req: AlertRequest, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO alerts (user_id, rule_type, threshold, enabled) VALUES ($1, $2, $3, $4) RETURNING *",
            uid, req.rule_type, req.threshold, req.enabled)
    return {"alert": {
        "id": str(row["id"]), "rule_type": row.get("rule_type", ""),
        "threshold": float(row.get("threshold") or 0), "enabled": row.get("enabled", True),
        "created_at": row["created_at"].isoformat() if isinstance(row.get("created_at"), datetime) else str(row.get("created_at","")),
    }}


@app.put("/api/alerts/{alert_id}")
async def update_alert(alert_id: str, req: AlertUpdateRequest, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        existing = await conn.fetchrow("SELECT * FROM alerts WHERE id = $1 AND user_id = $2", alert_id, uid)
        if not existing:
            raise HTTPException(status_code=404, detail="Alert not found")
        updates = {}
        if req.threshold is not None: updates["threshold"] = req.threshold
        if req.enabled is not None: updates["enabled"] = req.enabled
        if updates:
            set_clause = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
            await conn.execute(f"UPDATE alerts SET {set_clause} WHERE id = $1 AND user_id = $2",
                               alert_id, uid, *updates.values())
        row = await conn.fetchrow("SELECT * FROM alerts WHERE id = $1", alert_id)
    return {"alert": {
        "id": str(row["id"]), "rule_type": row.get("rule_type", ""),
        "threshold": float(row.get("threshold") or 0), "enabled": row.get("enabled", True),
        "created_at": row["created_at"].isoformat() if isinstance(row.get("created_at"), datetime) else str(row.get("created_at","")),
    }}


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: str, user: dict = Depends(get_current_user)):
    uid = user.get("id", "")
    async with _get_db_conn() as conn:
        result = await conn.execute("DELETE FROM alerts WHERE id = $1 AND user_id = $2", alert_id, uid)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"deleted": True}


# ─── Copy Trade Signals ─────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals(limit: int = Query(20, ge=1, le=100), user: dict = Depends(get_current_user)):
    return {"signals": []}


# ─── Health Check ───────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    try:
        if db_pool is None:
            return JSONResponse({"status": "ok", "database": "in_memory"}, status_code=200)
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return JSONResponse({"status": "unhealthy", "error": str(e)}, status_code=503)


# ─── Serve Frontend (production) ────────────────────────────────────

_static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")
if os.path.isdir(_static_dir):
    _assets_dir = os.path.join(_static_dir, "static")
    if os.path.isdir(_assets_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory=_assets_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
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
