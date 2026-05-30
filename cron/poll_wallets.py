"""
Blockchain polling cron job for ChainWatch.
Fetches transactions from all tracked wallets.
Run every 5 minutes.
"""
import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import asyncpg
import httpx

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.blockchain import (
    EtherscanClient,
    SolscanClient,
    BlockchairClient,
    get_eth_price_usd
)
from services.telegram_alerts import send_whale_alert, send_copy_trade_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chainwatch-poller")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
) or os.environ.get("POSTGRES_CONNECTION_STRING")


async def get_tracked_wallets(conn) -> list:
    """Get all tracked wallets that need polling."""
    return await conn.fetch(
        """
        SELECT w.*, u.wallet_address as user_wallet
        FROM wallets w
        JOIN users u ON u.id = w.user_id
        ORDER BY w.chain, w.address
        """
    )


async def is_tx_known(conn, tx_hash: str, chain: str) -> bool:
    """Check if a transaction is already in the database."""
    row = await conn.fetchrow(
        "SELECT 1 FROM transactions WHERE tx_hash = $1 AND chain = $2",
        tx_hash, chain
    )
    return row is not None


async def store_transaction(conn, wallet_id: str, chain: str, tx: dict, prices: dict):
    """Store a new transaction in the database."""
    # Calculate USD value
    token = tx.get("token", chain)
    amount = Decimal(str(tx.get("amount", 0)))
    
    # Map chain tickers to price feed keys
    price_key = {
        "eth": "ETH",
        "sol": "SOL",
        "btc": "BTC",
    }.get(chain, token)
    
    usd_price = prices.get(price_key, 0)
    usd_value = float(amount) * usd_price if usd_price else 0
    
    await conn.execute(
        """
        INSERT INTO transactions
            (wallet_id, tx_hash, type, amount, token, usd_value, timestamp, chain, from_address, to_address)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT DO NOTHING
        """,
        wallet_id,
        tx["tx_hash"],
        tx.get("type", "unknown"),
        amount,
        token,
        usd_value,
        tx.get("timestamp", datetime.utcnow().isoformat()),
        chain,
        tx.get("from_address", ""),
        tx.get("to_address", "")
    )
    
    return usd_value


async def poll_wallet(conn, wallet: dict, clients: dict, prices: dict):
    """Poll a single wallet for new transactions."""
    chain = wallet["chain"]
    address = wallet["address"]
    wallet_id = str(wallet["id"])
    client = clients.get(chain)
    
    if not client:
        logger.warning(f"No client for chain {chain}")
        return 0
    
    new_count = 0
    
    try:
        if chain == "eth":
            txs = await client.get_transactions(address, limit=10)
            # Also get token transfers
            token_txs = await client.get_token_transfers(address, limit=10)
            txs.extend(token_txs)
        elif chain == "sol":
            txs = await client.get_transactions(address, limit=10)
        elif chain == "btc":
            txs = await client.get_transactions(address, limit=10)
        else:
            return 0
        
        for tx in txs:
            if await is_tx_known(conn, tx["tx_hash"], chain):
                continue
            
            usd_value = await store_transaction(conn, wallet_id, chain, tx, prices)
            new_count += 1
            
            # Send alerts for whale wallets
            if wallet.get("is_whale") and usd_value > 1000:
                await send_whale_alert(
                    chain=chain.upper(),
                    address=address,
                    tx_type=tx.get("type", "unknown"),
                    amount=tx.get("amount", 0),
                    token=tx.get("token", chain),
                    usd_value=usd_value,
                    tx_hash=tx["tx_hash"]
                )
                
                # Generate copy trade signal for buys
                if tx.get("type") == "receive" and usd_value > 5000:
                    await conn.execute(
                        """
                        INSERT INTO copy_trade_signals
                            (wallet_id, token_symbol, action, amount_usd, confidence_score)
                        VALUES ($1, $2, 'buy', $3, $4)
                        ON CONFLICT DO NOTHING
                        """,
                        wallet_id,
                        tx.get("token", "UNKNOWN"),
                        usd_value,
                        min(usd_value / 10000, 0.95)  # Simple confidence heuristic
                    )
                    
                    await send_copy_trade_signal(
                        chain=chain.upper(),
                        wallet_label=wallet.get("label", address[:10]),
                        action="buy",
                        token=tx.get("token", "UNKNOWN"),
                        amount_usd=usd_value,
                        confidence=min(usd_value / 10000, 0.95)
                    )
    
    except Exception as e:
        logger.error(f"Error polling {chain}:{address[:10]}... - {e}")
    
    return new_count


async def run_poll():
    """Main polling loop."""
    logger.info("Starting ChainWatch blockchain poll...")
    
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # Get price data
        prices = await get_eth_price_usd()
        logger.info(f"Prices: {prices}")
        
        # Initialize blockchain clients
        clients = {
            "eth": EtherscanClient(),
            "sol": SolscanClient(),
            "btc": BlockchairClient(),
        }
        
        wallets = await get_tracked_wallets(conn)
        if not wallets:
            logger.info("No wallets to poll")
            return
        
        logger.info(f"Polling {len(wallets)} wallets...")
        total_new = 0
        
        for wallet in wallets:
            count = await poll_wallet(conn, wallet, clients, prices)
            total_new += count
            # Rate limiting: small delay between wallets
            await asyncio.sleep(0.5)
        
        # Close clients
        for client in clients.values():
            await client.close()
        
        logger.info(f"Poll complete. {total_new} new transactions found.")
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_poll())
