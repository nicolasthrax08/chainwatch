"""
Telegram alert service for ChainWatch.
Sends alerts via the Hermes agent's Telegram integration.
"""
import os
import httpx
from typing import Optional

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://localhost:3000")


async def send_telegram_alert(
    message: str,
    chat_id: Optional[str] = None
) -> bool:
    """Send a Telegram alert via the bot."""
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat or not TELEGRAM_BOT_TOKEN:
        return False
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": target_chat,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
            )
            resp.raise_for_status()
            return True
    except Exception:
        return False


async def send_whale_alert(
    chain: str,
    address: str,
    tx_type: str,
    amount: float,
    token: str,
    usd_value: float,
    tx_hash: str
) -> bool:
    """Send a whale transaction alert."""
    chain_emoji = {"ETH": "🟣", "SOL": "🟦", "BTC": "🟡"}.get(chain, "⚪")
    direction = "📥" if tx_type == "receive" else "📤"
    
    message = (
        f"{chain_emoji} <b>Whale Alert ({chain})</b>\n\n"
        f"{direction} <b>{tx_type.upper()}</b>\n"
        f"Amount: <code>{amount:,.6f} {token}</code>\n"
        f"Value: <code>${usd_value:,.2f}</code>\n"
        f"Address: <code>{address[:10]}...{address[-6:]}</code>\n\n"
        f"TX: <code>{tx_hash[:20]}...</code>"
    )
    return await send_telegram_alert(message)


async def send_portfolio_alert(
    alert_type: str,
    value: float,
    threshold: float
) -> bool:
    """Send a portfolio change alert."""
    emoji = "🔴" if value < 0 else "🟢"
    message = (
        f"{emoji} <b>Portfolio Alert</b>\n\n"
        f"Type: {alert_type}\n"
        f"Change: {value:+.2f}%\n"
        f"Threshold: {threshold:.2f}%"
    )
    return await send_telegram_alert(message)


async def send_copy_trade_signal(
    chain: str,
    wallet_label: str,
    action: str,
    token: str,
    amount_usd: float,
    confidence: float
) -> bool:
    """Send a copy trade signal notification."""
    message = (
        f"🔄 <b>Copy Trade Signal</b>\n\n"
        f"Whale: <b>{wallet_label}</b> ({chain.upper()})\n"
        f"Action: <b>{action.upper()}</b>\n"
        f"Token: <code>{token}</code>\n"
        f"Amount: <code>${amount_usd:,.2f}</code>\n"
        f"Confidence: <b>{confidence:.0%}</b>\n\n"
        f"Use /mirror to copy this trade"
    )
    return await send_telegram_alert(message)
