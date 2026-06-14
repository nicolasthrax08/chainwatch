"""
Blockchain API services for ChainWatch.
Supports Etherscan (EVM), Solscan (Solana), Blockchair (Bitcoin).
All free-tier APIs with rate limiting.
"""
import os
import httpx
import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timezone

ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
SOLSCAN_API_KEY = os.environ.get("SOLSCAN_API_KEY", "")
BLOCKCHAIR_API_KEY = os.environ.get("BLOCKCHAIR_API_KEY", "")

# API base endpoints
ETHERSCAN_BASE = "https://api.etherscan.io/api"
SOLSCAN_BASE = "https://public-api.solscan.io"
BLOCKCHAIR_BASE = "https://api.blockchair.com/bitcoin"
MEMPOOL_BASE = "https://mempool.space/api"
BLOCKCYPHER_BASE = "https://api.blockcypher.com/v1/btc/main"
ETH_RPC_URL = "https://ethereum-rpc.publicnode.com"
SOL_RPC_URL = "https://api.mainnet-beta.solana.com"


class EtherscanClient:
    """Ethereum/EVM balance via publicnode JSON-RPC (free, no key needed)."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_eth_balance(self, address: str) -> Dict:
        """Get ETH balance for an address via publicnode JSON-RPC."""
        client = await self._get_client()
        try:
            resp = await client.post(
                ETH_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getBalance",
                    "params": [address, "latest"],
                    "id": 1
                }
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                wei = int(data["result"], 16)
                return {
                    "address": address,
                    "balance_eth": wei / 1e18,
                    "balance_wei": wei
                }
            return {"address": address, "balance_eth": 0, "error": data.get("error", "Unknown error")}
        except httpx.HTTPError as e:
            return {"address": address, "balance_eth": 0, "error": str(e)}

    async def get_transactions(self, address: str, limit: int = 20) -> List[Dict]:
        """Get normal transactions for an address (requires Etherscan API key with V2)."""
        # For now return empty — would need Etherscan Pro or alternative
        return []


class SolscanClient:
    """Solana balance via public JSON-RPC (free, no key needed)."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_balance(self, address: str) -> Dict:
        """Get SOL balance via Solana public RPC."""
        client = await self._get_client()
        try:
            resp = await client.post(
                SOL_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [address]
                }
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                lamports = data["result"].get("value", 0)
                return {
                    "address": address,
                    "balance_sol": lamports / 1e9,
                    "balance_lamports": lamports
                }
            return {"address": address, "balance_sol": 0, "error": str(data.get("error", ""))}
        except httpx.HTTPError as e:
            return {"address": address, "balance_sol": 0, "error": str(e)}

    async def get_transactions(self, address: str, limit: int = 20) -> List[Dict]:
        """Get recent transactions (simplified)."""
        return []


class BlockchairClient:
    """Bitcoin balance via mempool.space (free, no key needed)."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_balance(self, address: str) -> Dict:
        """Get BTC balance via mempool.space API (free, no key)."""
        client = await self._get_client()
        try:
            resp = await client.get(f"{MEMPOOL_BASE}/address/{address}")
            resp.raise_for_status()
            data = resp.json()
            stats = data.get("chain_stats", {})
            funded = stats.get("funded_txo_sum", 0)
            spent = stats.get("spent_txo_sum", 0)
            balance_sat = funded - spent
            return {
                "address": address,
                "balance_btc": balance_sat / 1e8,
                "balance_satoshis": balance_sat,
                "tx_count": stats.get("tx_count", 0)
            }
        except Exception as e:
            # Fallback: try BlockCypher on any failure (HTTP, JSON decode, etc.)
            try:
                resp = await client.get(
                    f"{BLOCKCYPHER_BASE}/addrs/{address}/balance"
                )
                resp.raise_for_status()
                data = resp.json()
                balance_sat = data.get("balance", 0)
                return {
                    "address": address,
                    "balance_btc": balance_sat / 1e8,
                    "balance_satoshis": balance_sat,
                    "tx_count": data.get("n_tx", 0)
                }
            except Exception as e2:
                return {"address": address, "balance_btc": 0, "error": str(e2)}

    async def get_transactions(self, address: str, limit: int = 20) -> List[Dict]:
        """Get recent transactions via mempool.space."""
        client = await self._get_client()
        try:
            resp = await client.get(f"{MEMPOOL_BASE}/address/{address}/txs")
            resp.raise_for_status()
            txs_data = resp.json()
            txs = []
            for tx in txs_data[:limit]:
                txid = tx.get("txid", "")
                status = tx.get("status", {})
                txs.append({
                    "tx_hash": txid,
                    "type": "unknown",
                    "amount": 0,
                    "token": "BTC",
                    "timestamp": datetime.fromtimestamp(
                        status.get("block_time", 0), timezone.utc
                    ).isoformat() if status.get("block_time") else "",
                    "confirmed": status.get("confirmed", False),
                    "block_height": status.get("block_height", 0),
                })
            return txs
        except httpx.HTTPError:
            return []


# ─── Price feeds ────────────────────────────────────────────────────

async def get_eth_price_usd() -> dict:
    """Get current crypto prices in USD from CoinGecko (free, no key needed)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum,solana,bitcoin", "vs_currencies": "usd"}
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "ETH": data.get("ethereum", {}).get("usd", 0),
                "SOL": data.get("solana", {}).get("usd", 0),
                "BTC": data.get("bitcoin", {}).get("usd", 0),
            }
    except Exception:
        return {"ETH": 0, "SOL": 0, "BTC": 0}
