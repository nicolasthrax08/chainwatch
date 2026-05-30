"""
Blockchain API services for ChainWatch.
Supports Etherscan (EVM), Solscan (Solana), Blockchair (Bitcoin).
All free-tier APIs with rate limiting.
"""
import os
import httpx
import asyncio
from typing import List, Dict, Optional
from datetime import datetime

ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
SOLSCAN_API_KEY = os.environ.get("SOLSCAN_API_KEY", "")
BLOCKCHAIR_API_KEY = os.environ.get("BLOCKCHAIR_API_KEY", "")

ETHERSCAN_BASE = "https://api.etherscan.io/api"
SOLSCAN_BASE = "https://public-api.solscan.io"
BLOCKCHAIR_BASE = "https://api.blockchair.com/bitcoin"


class EtherscanClient:
    """Etherscan API client for Ethereum/EVM chains."""
    
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ETHERSCAN_API_KEY
        self.base_url = ETHERSCAN_BASE
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
        """Get ETH balance for an address."""
        client = await self._get_client()
        resp = await client.get(self.base_url, params={
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
            "apikey": self.api_key
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1":
            wei = int(data["result"])
            return {
                "address": address,
                "balance_eth": wei / 1e18,
                "balance_wei": wei
            }
        return {"address": address, "balance_eth": 0, "error": data.get("message")}
    
    async def get_transactions(
        self, address: str, start_block: int = 0, limit: int = 20
    ) -> List[Dict]:
        """Get normal transactions for an address."""
        client = await self._get_client()
        resp = await client.get(self.base_url, params={
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": start_block,
            "endblock": 99999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",
            "apikey": self.api_key
        })
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") != "1":
            return []
        
        txs = []
        for tx in data.get("result", []):
            value_eth = int(tx.get("value", 0)) / 1e18
            gas_price = int(tx.get("gasPrice", 0)) / 1e9
            gas_used = int(tx.get("gasUsed", 0))
            
            txs.append({
                "tx_hash": tx["hash"],
                "type": "receive" if tx["to"].lower() == address.lower() else "send",
                "amount": abs(value_eth),
                "token": "ETH",
                "from_address": tx.get("from", ""),
                "to_address": tx.get("to", ""),
                "timestamp": datetime.utcfromtimestamp(
                    int(tx.get("timeStamp", 0))
                ).isoformat(),
                "block_number": int(tx.get("blockNumber", 0)),
                "gas_fee_eth": (gas_price * gas_used) / 1e9,
                "confirmations": int(tx.get("confirmations", 0)),
            })
        return txs
    
    async def get_token_transfers(
        self, address: str, limit: int = 20
    ) -> List[Dict]:
        """Get ERC-20 token transfers for an address."""
        client = await self._get_client()
        resp = await client.get(self.base_url, params={
            "module": "account",
            "action": "tokentx",
            "address": address,
            "page": 1,
            "offset": limit,
            "sort": "desc",
            "apikey": self.api_key
        })
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") != "1":
            return []
        
        txs = []
        for tx in data.get("result", []):
            decimals = int(tx.get("tokenDecimal", 18))
            value = int(tx.get("value", 0)) / (10 ** decimals)
            
            txs.append({
                "tx_hash": tx["hash"],
                "type": "receive" if tx["to"].lower() == address.lower() else "send",
                "amount": abs(value),
                "token": tx.get("tokenSymbol", "UNKNOWN"),
                "token_address": tx.get("contractAddress", ""),
                "from_address": tx.get("from", ""),
                "to_address": tx.get("to", ""),
                "timestamp": datetime.utcfromtimestamp(
                    int(tx.get("timeStamp", 0))
                ).isoformat(),
            })
        return txs


class SolscanClient:
    """Solscan API client for Solana."""
    
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or SOLSCAN_API_KEY
        self.base_url = SOLSCAN_BASE
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30,
                headers={"Token": self.api_key} if self.api_key else {},
                limits=httpx.Limits(max_connections=5)
            )
        return self._client
    
    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def get_balance(self, address: str) -> Dict:
        """Get SOL balance for an address."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/account/{address}"
            )
            resp.raise_for_status()
            data = resp.json()
            lamports = data.get("lamports", 0)
            return {
                "address": address,
                "balance_sol": lamports / 1e9,
                "balance_lamports": lamports
            }
        except httpx.HTTPError as e:
            return {"address": address, "balance_sol": 0, "error": str(e)}
    
    async def get_transactions(
        self, address: str, limit: int = 20
    ) -> List[Dict]:
        """Get recent transactions for a Solana address."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/account/transactions",
                params={"account": address, "limit": limit}
            )
            resp.raise_for_status()
            data = resp.json()
            
            txs = []
            for tx in data if isinstance(data, list) else data.get("data", []):
                txs.append({
                    "tx_hash": tx.get("txHash", tx.get("signature", "")),
                    "type": "receive",  # Simplified; real implementation would parse instructions
                    "amount": 0,  # Would need to parse token balances
                    "token": "SOL",
                    "timestamp": tx.get("blockTime", ""),
                    "slot": tx.get("slot", 0),
                    "fee": tx.get("fee", 0) / 1e9,
                    "status": "success" if tx.get("status") == "Success" else "failed",
                })
            return txs
        except httpx.HTTPError as e:
            return []


class BlockchairClient:
    """Blockchair API client for Bitcoin."""
    
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or BLOCKCHAIR_API_KEY
        self.base_url = BLOCKCHAIR_BASE
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
        """Get BTC balance for an address."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/dashboards/address/{address}",
                params={"key": self.api_key} if self.api_key else {}
            )
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("data") and address in data["data"]:
                addr_data = data["data"][address]
                balance_sat = addr_data.get("address", {}).get("balance", 0)
                return {
                    "address": address,
                    "balance_btc": balance_sat / 1e8,
                    "balance_satoshis": balance_sat,
                    "tx_count": addr_data.get("address", {}).get("transaction_count", 0)
                }
            return {"address": address, "balance_btc": 0, "error": "Address not found"}
        except httpx.HTTPError as e:
            return {"address": address, "balance_btc": 0, "error": str(e)}
    
    async def get_transactions(
        self, address: str, limit: int = 20
    ) -> List[Dict]:
        """Get recent transactions for a Bitcoin address."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/dashboards/address/{address}",
                params={
                    "limit": limit,
                    "key": self.api_key
                } if self.api_key else {"limit": limit}
            )
            resp.raise_for_status()
            data = resp.json()
            
            if not data.get("data") or address not in data["data"]:
                return []
            
            addr_data = data["data"][address]
            txs = []
            for tx_hash in addr_data.get("transactions", [])[:limit]:
                txs.append({
                    "tx_hash": tx_hash,
                    "type": "unknown",
                    "amount": 0,
                    "token": "BTC",
                    "timestamp": "",
                })
            return txs
        except httpx.HTTPError as e:
            return []


# ─── Price feeds ────────────────────────────────────────────────────

async def get_eth_price_usd() -> float:
    """Get current ETH price in USD from CoinGecko (free, no key needed)."""
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
