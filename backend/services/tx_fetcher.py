"""
Transaction fetching utilities for ChainWatch.
Fetches live on-chain transactions from free public APIs.
All fetchers use a shared singleton httpx.AsyncClient, retry with backoff on 429s,
and are concurrency-capped per upstream to avoid starving free public RPCs.
"""
import httpx
import asyncio
import logging
import random
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─── API endpoints (free, no key needed) ────────────────────────────
MEMPOOL_BASE = "https://mempool.space/api"
SOL_RPC_URL = "https://api.mainnet-beta.solana.com"
BLOCKCYPHER_BASE = "https://api.blockcypher.com/v1/btc/main"

# ─── Whale thresholds ───────────────────────────────────────────────
WHALE_THRESHOLDS = {
    "btc": 5.0,    # BTC
    "eth": 50.0,   # ETH
    "sol": 500.0,  # SOL
}

# ─── HTTP client singleton (lock-protected, F7) ─────────────────────
_http_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

# ─── Per-upstream concurrency caps (F2) ────────────────────────────
_sol_rpc_sem = asyncio.Semaphore(5)
_mempool_sem = asyncio.Semaphore(8)
_etherscan_sem = asyncio.Semaphore(3)
_blockcypher_sem = asyncio.Semaphore(3)

# ─── Retry config (F3) ──────────────────────────────────────────────
MAX_RETRIES = 2
BASE_DELAY = 1.0  # seconds


async def _get_client() -> httpx.AsyncClient:
    """Get or lazily create the shared HTTP client (thread-/coroutine-safe)."""
    global _http_client
    async with _client_lock:
        if _http_client is None or _http_client.is_closed:
            if _http_client and not _http_client.is_closed:
                await _http_client.aclose()
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _http_client


async def close_client():
    """Close the shared HTTP client on shutdown."""
    global _http_client
    async with _client_lock:
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
            _http_client = None


# ─── Retry helper with 429 backoff (F3) ─────────────────────────────
async def _retry_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """HTTP request with automatic retry on 429 / transient errors."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429:
                delay = float(
                    resp.headers.get("Retry-After", BASE_DELAY * (2 ** attempt))
                )
                logger.warning(f"429 from {url}, retry in {delay}s (attempt {attempt + 1})")
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < MAX_RETRIES:
                await asyncio.sleep(
                    BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                )
                continue
            raise
    raise RuntimeError(f"Exhausted retries for {url}")


# ─── BTC: mempool.space ─────────────────────────────────────────────
async def fetch_btc_transactions(
    address: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Fetch recent BTC transactions from mempool.space (free, no key)."""
    client = await _get_client()
    try:
        async with _mempool_sem:
            resp = await _retry_request(
                client, "GET", f"{MEMPOOL_BASE}/address/{address}/txs"
            )
        txs_data = resp.json()
        results: List[Dict[str, Any]] = []
        for tx in txs_data[:limit]:
            status = tx.get("status", {})
            block_time = status.get("block_time", 0)
            # Net amount for this address (vin = spent by us, vout = received by us)
            vin_total = sum(
                v.get("prevout", {}).get("value", 0)
                for v in tx.get("vin", [])
                if v.get("prevout", {}).get("scriptpubkey_address") == address
            )
            vout_total = sum(
                v.get("value", 0)
                for v in tx.get("vout", [])
                if v.get("scriptpubkey_address") == address
            )
            net_sat = vout_total - vin_total
            amount_btc = abs(net_sat) / 1e8
            results.append({
                "tx_hash": tx.get("txid", ""),
                "type": (
                    "receive" if net_sat > 0 else
                    "send" if net_sat < 0 else "unknown"
                ),
                "amount": str(round(amount_btc, 8)),
                "token": "BTC",
                "usd_value": 0,  # Filled by caller with price data
                "timestamp": (
                    datetime.fromtimestamp(block_time, timezone.utc).isoformat()
                    if block_time else ""
                ),
                "chain": "btc",
                "status": "confirmed" if status.get("confirmed") else "pending",
                "is_confirmed": status.get("confirmed", False),
            })
        return results
    except Exception as e:
        logger.warning(f"mempool.space tx fetch failed for {address}: {e}")
        # Fallback: BlockCypher (F11 — include best-effort timestamp)
        try:
            async with _blockcypher_sem:
                resp = await _retry_request(
                    client, "GET",
                    f"{BLOCKCYPHER_BASE}/addrs/{address}",
                    params={"limit": limit},
                )
            data = resp.json()
            results = []
            for tx in data.get("txrefs", [])[:limit]:
                # BlockCypher returns ISO date strings for 'confirmed' / 'received'
                ts_raw = tx.get("confirmed", "") or tx.get("received", "")
                ts_str = ts_raw[:19] if ts_raw else ""  # Trim to ISO-8601 date
                results.append({
                    "tx_hash": tx.get("tx_hash", ""),
                    "type": "unknown",  # BlockCypher txrefs lack direction info
                    "amount": str(abs(tx.get("value", 0)) / 1e8),
                    "token": "BTC",
                    "usd_value": 0,
                    "timestamp": ts_str,
                    "chain": "btc",
                    "status": (
                        "confirmed" if tx.get("confirmations", 0) > 0
                        else "pending"
                    ),
                    "is_confirmed": tx.get("confirmations", 0) > 0,
                })
            return results
        except Exception as e2:
            logger.warning(f"BlockCypher fallback also failed for {address}: {e2}")
            return []


# ─── ETH: Etherscan (requires API key) ─────────────────────────────
async def fetch_eth_transactions(
    address: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Fetch recent ETH transactions via Etherscan API key (free tier OK).
    Without an Etherscan API key, returns empty list immediately.
    """
    import os
    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    if not api_key:
        logger.info("No Etherscan API key — skipping live ETH tx fetch")
        return []

    client = await _get_client()
    try:
        async with _etherscan_sem:
            resp = await _retry_request(
                client,
                "GET",
                "https://api.etherscan.io/api",
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": limit,
                    "sort": "desc",
                    "apikey": api_key,
                },
            )
        data = resp.json()
        if data.get("status") != "1":
            # F6: Log rate-limit / error messages instead of silently swallowing
            result_str = str(data.get("result", ""))[:200]
            msg = data.get("message", "unknown")
            if "rate limit" in result_str.lower():
                logger.warning(f"Etherscan rate-limited for {address}: {result_str}")
            elif "invalid api key" in result_str.lower():
                logger.warning(f"Etherscan invalid API key for {address}")
            else:
                logger.info(
                    f"Etherscan status=0 for {address}: {msg} / {result_str}"
                )
            return []

        results: List[Dict[str, Any]] = []
        for tx in data.get("result", []):
            value_eth = int(tx.get("value", 0)) / 1e18
            is_receive = tx.get("to", "").lower() == address.lower()
            ts = int(tx.get("timeStamp", 0))
            results.append({
                "tx_hash": tx.get("hash", ""),
                "type": "receive" if is_receive else "send",
                "amount": str(round(abs(value_eth), 8)),
                "token": "ETH",
                "usd_value": 0,
                "timestamp": (
                    datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""
                ),
                "chain": "eth",
                "status": (
                    "confirmed" if int(tx.get("confirmations", 0)) > 0
                    else "pending"
                ),
                "is_confirmed": int(tx.get("confirmations", 0)) > 0,
            })
        return results
    except Exception as e:
        logger.warning(f"Etherscan tx fetch failed for {address}: {e}")
        return []


# ─── SOL: Solana JSON-RPC (concurrent, F1) ─────────────────────────
async def fetch_sol_transactions(
    address: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Fetch recent SOL transactions via Solana public JSON-RPC.
    All getTransaction calls are fired concurrently to avoid O(N × RTT) latency.
    """
    client = await _get_client()
    try:
        async with _sol_rpc_sem:
            sig_resp = await _retry_request(
                client,
                "POST",
                SOL_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [address, {"limit": limit}],
                },
            )
        sig_data = sig_resp.json()
        signatures = sig_data.get("result", [])
        if not signatures:
            return []

        # Fetch all transaction details concurrently (F1 fix)
        async def _fetch_one(sig: str) -> Optional[Dict[str, Any]]:
            try:
                async with _sol_rpc_sem:
                    tx_resp = await _retry_request(
                        client,
                        "POST",
                        SOL_RPC_URL,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [
                                sig,
                                {
                                    "encoding": "jsonParsed",
                                    "maxSupportedTransactionVersion": 0,
                                },
                            ],
                        },
                    )
                result = tx_resp.json().get("result")
                if not result:
                    return None

                block_time = result.get("blockTime", 0)
                meta = result.get("meta", {})
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                account_keys = (
                    result.get("transaction", {})
                    .get("message", {})
                    .get("accountKeys", [])
                )

                # Find the index of our address in account keys
                addr_idx = None
                for i, key in enumerate(account_keys):
                    if isinstance(key, dict) and key.get("pubkey") == address:
                        addr_idx = i
                        break
                    elif isinstance(key, str) and key == address:
                        addr_idx = i
                        break

                net_sol = 0.0
                if (
                    addr_idx is not None
                    and addr_idx < len(pre_balances)
                    and addr_idx < len(post_balances)
                ):
                    net_sol = (
                        post_balances[addr_idx] - pre_balances[addr_idx]
                    ) / 1e9

                # FIX: Do NOT clamp net_sol to 0 — negative values indicate sends.
                # max(net_sol, 0) caused all outgoing SOL txs to be classified as "unknown".
                tx_type = (
                    "receive" if net_sol > 0 else
                    "send" if net_sol < 0 else "unknown"
                )
                err = meta.get("err")
                return {
                    "tx_hash": sig,
                    "type": tx_type,
                    "amount": str(round(abs(net_sol), 8)),
                    "token": "SOL",
                    "usd_value": 0,
                    "timestamp": (
                        datetime.fromtimestamp(block_time, timezone.utc).isoformat()
                        if block_time else ""
                    ),
                    "chain": "sol",
                    "status": "failed" if err else "confirmed",
                    "is_confirmed": err is None,
                }
            except Exception as e:
                logger.debug(f"SOL tx fetch error {sig[:16]}…: {e}")
                return None

        sigs = [
            s["signature"] for s in signatures[:limit] if s.get("signature")
        ]
        raw_results = await asyncio.gather(
            *[_fetch_one(sig) for sig in sigs], return_exceptions=True
        )
        # Filter out Nones and exceptions (F9: partial error isolation)
        results: List[Dict[str, Any]] = []
        for r in raw_results:
            if isinstance(r, dict):
                results.append(r)
            elif isinstance(r, BaseException):
                logger.debug(f"SOL batch tx error: {r}")
        return results

    except Exception as e:
        logger.warning(f"Solana tx fetch failed for {address}: {e}")
        return []


# ─── Dispatcher ─────────────────────────────────────────────────────
async def fetch_transactions_for_wallet(
    address: str, chain: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Route to the correct chain fetcher based on chain code."""
    fetchers = {
        "btc": fetch_btc_transactions,
        "eth": fetch_eth_transactions,
        "sol": fetch_sol_transactions,
    }
    fetcher = fetchers.get(chain)
    if not fetcher:
        logger.warning(f"Unknown chain '{chain}' for address {address}")
        return []
    return await fetcher(address, limit)


async def fetch_transactions_for_wallets(
    wallets: list, limit: int = 10
) -> list:
    """
    Fetch live transactions for multiple wallets concurrently.
    Returns a flat list of transactions, each annotated with a copy of wallet info.
    Results are sorted by timestamp descending.
    """
    tasks = [
        fetch_transactions_for_wallet(w["address"], w["chain"], limit)
        for w in wallets
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_txs: List[Dict[str, Any]] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.warning(
                f"Tx fetch error for wallet {wallets[i]['address']}: {result}"
            )
            continue
        assert isinstance(result, list)
        wallet = wallets[i]
        for tx in result:
            # F4: Always copy before annotating to avoid mutating shared dicts
            all_txs.append({
                **tx,
                "wallet_label": wallet.get("label", ""),
                "wallet_address": wallet["address"],
                "chain": wallet["chain"],
            })

    # Sort by timestamp descending (empty strings sort to the bottom)
    all_txs.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return all_txs


# ─── Whale detection & risk profiling ───────────────────────────────

def classify_wallet(
    balance_native: float, chain: str, tx_count: int
) -> Dict[str, Any]:
    """
    Classify a wallet based on its on-chain balance and activity.
    Returns flags: is_whale, is_fresh_wallet, risk_label.

    - is_whale: balance exceeds chain-specific threshold (>= comparison)
    - is_fresh_wallet: zero on-chain transactions (tx_count == 0)
    - risk_label: "whale" | "fresh_wallet" | "normal"
    """
    threshold = WHALE_THRESHOLDS.get(chain, float("inf"))
    is_whale = balance_native >= threshold  # F10: >= not >
    is_fresh_wallet = tx_count == 0

    if is_whale:
        risk_label = "whale"
    elif is_fresh_wallet:
        risk_label = "fresh_wallet"
    else:
        risk_label = "normal"

    return {
        "is_whale": is_whale,
        "is_fresh_wallet": is_fresh_wallet,
        "risk_label": risk_label,
    }
