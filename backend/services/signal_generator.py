"""
Signal generation service.
Called after monitor detects a new transaction on a whale wallet.
Inserts into copy_trade_signals if the tx meets signal criteria.
"""
import logging
import math
import time
from datetime import datetime
from typing import Optional, Dict, Tuple

logger = logging.getLogger("chainwatch.signals")

# Chain-specific minimum tx value to generate a signal (Pitfall #24: event-level amounts).
# BTC whales moving <$10K are likely dust; ETH and SOL have proportionally lower floors.
MIN_SIGNAL_USD_BY_CHAIN: dict = {
    "btc": 10000.0,
    "eth": 5000.0,
    "sol": 2000.0,
}
MIN_SIGNAL_USD_DEFAULT = 5000.0  # fallback for any future chain
DEDUP_INTERVAL = "5 minutes"

# Minimum whale score threshold (0.0-1.0) for signal generation.
# Wallets with whale_score below this value will not generate signals,
# even if the transaction amount exceeds MIN_SIGNAL_USD_BY_CHAIN.
# Default: 0.30 (30%), filters out the weakest whale wallets while
# still capturing mid-tier whales. Raised from 0.20 in cycle
# 2026-06-12 after threshold review found 0.20 too lenient —
# at 0.20, C_final is capped at 0.60 for borderline whales.
MIN_WHALE_SCORE = 0.30

# Finding 5: In-memory dedup cache with TTL to prevent signal dedup race condition
# Maps (wallet_id, token_symbol, action) → insertion_timestamp
_signal_dedup_cache: Dict[Tuple[str, str, str], float] = {}
_DEDUP_TTL_SECONDS = 300  # 5 minutes, matches DEDUP_INTERVAL


def _is_duplicate(wallet_id: str, token_symbol: str, action: str) -> bool:
    """Check if a signal is a duplicate based on in-memory cache (Finding 5)."""
    _prune_dedup_cache()
    key = (wallet_id, token_symbol.upper(), action)
    return key in _signal_dedup_cache


def _mark_signal(wallet_id: str, token_symbol: str, action: str) -> None:
    """Mark a signal as recently inserted (Finding 5)."""
    key = (wallet_id, token_symbol.upper(), action)
    _signal_dedup_cache[key] = time.time()


def _prune_dedup_cache() -> None:
    """Remove expired entries from the dedup cache."""
    now = time.time()
    expired = [k for k, v in _signal_dedup_cache.items() if now - v > _DEDUP_TTL_SECONDS]
    for k in expired:
        del _signal_dedup_cache[k]


def _fmt_amount(amount_usd: float) -> str:
    """Format USD amount: <$10K → '$X,XXX' / ≥$10K → '$XX.XK' / ≥$1M → '$X.XM'."""
    if amount_usd >= 1_000_000:
        return f"${amount_usd / 1_000_000:.1f}M"
    elif amount_usd >= 10_000:
        return f"${amount_usd / 1_000:.1f}K"
    else:
        return f"${amount_usd:,.0f}"


def _resolve_label(wallet_label: Optional[str], wallet_address: str) -> str:
    """Return wallet label if set, else 'Whale 0xabc...'."""
    if wallet_label and wallet_label.strip():
        return wallet_label.strip()
    return f"Whale {wallet_address[:6]}..."


def generate_explanation(
    signal_data: dict,
    whale_score: float,
    median_amount_30d: float,
) -> str:
    """
    Generate a one-sentence plain-English explanation for a copy-trade signal.

    Args:
        signal_data: dict with keys:
            action, amount_usd, token_symbol, wallet_label, wallet_address,
            is_receive (bool), confidence_score (C_tx), confidence_final (C_final)
        whale_score: W from whale_scorer (effective score, 0-1)
        median_amount_30d: median_signal_amount_30d from whale_scorer

    Returns:
        Explanation string, max 120 chars.
    """
    action = signal_data.get("action", "")
    amount_usd = signal_data.get("amount_usd", 0) or 0
    token_symbol = signal_data.get("token_symbol", "")
    wallet_label_raw = signal_data.get("wallet_label")
    wallet_address = signal_data.get("wallet_address", "")
    is_receive = signal_data.get("is_receive", action == "receive")
    c_tx = signal_data.get("confidence_score", 0) or 0
    c_final = signal_data.get("confidence_final", c_tx) or c_tx

    label = _resolve_label(wallet_label_raw, wallet_address)
    amount_formatted = _fmt_amount(amount_usd)

    is_new = whale_score < 0.5
    is_proven = not is_new

    # Determine if trade is "large" relative to wallet's typical trade size
    if median_amount_30d and median_amount_30d > 0:
        ratio = amount_usd / median_amount_30d
        if ratio >= 2.0:
            size_bucket = "large"
        elif ratio >= 0.5:
            size_bucket = "med"
        else:
            size_bucket = "small"
    else:
        size_bucket = "med"

    # Confidence tiers
    if c_final >= 0.75:
        conf_tier = "high"
    elif c_final >= 0.55:
        conf_tier = "med"
    else:
        conf_tier = "low"

    # ── Decision tree (priority order) ─────────────────────────────────
    # TPL-A: receive + new whale
    if is_receive and is_new:
        tpl = f"{label} received {amount_formatted} in {token_symbol} — first signal from this wallet."
    # TPL-B: receive + proven + large
    elif is_receive and is_proven and size_bucket == "large":
        exec_rate = signal_data.get("execution_rate_30d", 0) or 0
        rate_pct = int(round(exec_rate * 100))
        tpl = f"{label} received {amount_formatted} in {token_symbol} — consistent whale with {rate_pct}% execution rate."
    # TPL-C: receive + proven + non-large
    elif is_receive and is_proven:
        tpl = f"{label} received {amount_formatted} in {token_symbol}."
    # TPL-D: buy + proven + high + large
    elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "large":
        tpl = f"{label} bought {amount_formatted} of {token_symbol} — above average size for this proven whale."
    # TPL-E: buy + proven + high + med
    elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "med":
        tpl = f"{label} bought {amount_formatted} of {token_symbol} — strong confidence trade."
    # TPL-F: buy + proven + high + small
    elif action == "buy" and is_proven and conf_tier == "high" and size_bucket == "small":
        tpl = f"{label} bought {amount_formatted} of {token_symbol} — notable despite smaller size."
    # TPL-G: buy + proven + med/low (any size)
    elif action == "buy" and is_proven:
        tpl = f"{label} bought {amount_formatted} of {token_symbol} — moderate confidence."
    # TPL-H: buy + new + high
    elif action == "buy" and is_new and conf_tier == "high":
        tpl = f"New whale {label} bought {amount_formatted} of {token_symbol} — large trade, watch closely."
    # TPL-I: buy + new + med
    elif action == "buy" and is_new and conf_tier == "med":
        tpl = f"New whale {label} bought {amount_formatted} of {token_symbol} — medium confidence signal."
    # TPL-J: buy + new + low + small
    elif action == "buy" and is_new and conf_tier == "low" and size_bucket == "small":
        tpl = f"New whale {label} bought {amount_formatted} of {token_symbol} — small trade, low confidence."
    # TPL-K: buy + new + low + med/large
    elif action == "buy" and is_new and conf_tier == "low":
        tpl = f"New whale {label} bought {amount_formatted} of {token_symbol} — unproven whale, treat cautiously."
    # TPL-Z: fallback
    else:
        w_pct = int(round(whale_score * 100))
        c_pct = int(round(c_final * 100))
        tpl = f"{label} {action} {amount_formatted} of {token_symbol} — whale score: {w_pct}%, confidence: {c_pct}%."

    if len(tpl) > 120:
        tpl = tpl[:117] + "..."
    return tpl


async def evaluate_for_signal(
    conn,
    wallet_id: str,
    is_whale: bool,
    user_id: str,
    chain: str,
    tx_hash: str,
    tx_type: str,
    token: str,
    tx_amount_native: float,
    price_usd: float,
    whale_score: float = 0.0,
    median_amount_30d: float = 0.0,
    execution_rate_30d: float = 0.0,
    wallet_label: Optional[str] = None,
    wallet_address: str = "",
) -> Optional[dict]:
    """
    Evaluate a new transaction from a whale wallet for signal generation.

    Args:
        conn: active asyncpg connection (caller manages transaction)
        wallet_id: UUID of the wallet
        is_whale: pre-resolved from Phase 1 data (avoids redundant SELECT)
        user_id: pre-resolved from Phase 1 data
        chain: chain code (eth/sol/btc)
        tx_hash: the new transaction hash
        tx_type: transaction type from tx_fetcher (buy/receive/send/...)
        token: token symbol from tx_fetcher
        tx_amount_native: the actual transaction amount in native units
        price_usd: current price of the token in USD (from monitor's _price_cache)

    Returns:
        The created signal dict if one was generated, else None.
    """
    # Fast-path: skip non-whale wallets
    if not is_whale:
        return None

    # Only generate buy signals
    if tx_type not in ("buy", "receive"):
        return None

    # Normalize args
    if not tx_amount_native or not price_usd:
        return None

    amount_usd = tx_amount_native * price_usd
    # Use chain-specific minimum threshold to filter dust txns from whales
    _min_usd = MIN_SIGNAL_USD_BY_CHAIN.get(chain.lower(), MIN_SIGNAL_USD_DEFAULT)
    if amount_usd < _min_usd:
        return None

    # Whale score threshold: skip signals from wallets with very low whale scores
    # This prevents balance-only whales with no signal history from generating noise
    if whale_score < MIN_WHALE_SCORE:
        logger.debug(
            "Signal suppressed: wallet=%s whale_score=%.2f < MIN_WHALE_SCORE=%.2f",
            wallet_id, whale_score, MIN_WHALE_SCORE,
        )
        return None

    # Normalize token symbol
    token_symbol = (token or chain.upper()).strip().upper()
    if not token_symbol:
        return None

    # Confidence: log-scaled from $1k (0.5) to $1M+ (1.0)
    raw = math.log10(max(amount_usd, 1000)) - 3   # 0 at $1k, 3 at $1M
    confidence = round(min(0.5 + (raw / 3) * 0.5, 1.0), 2)

    # Dedup: avoid duplicate signals for the same wallet+token within 5 minutes
    # Finding 5: In-memory check first (atomic within event loop), then DB check
    _is_dup = _is_duplicate(wallet_id, token_symbol, tx_type)
    if _is_dup:
        return None

    existing = await conn.fetchval(
        """
        SELECT id FROM copy_trade_signals
        WHERE wallet_id = $1 AND token_symbol = $2
          AND created_at > NOW() - INTERVAL '5 minutes'
        """,
        wallet_id, token_symbol,
    )
    if existing:
        return None

    # Mark in in-memory cache BEFORE inserting (Finding 5: prevent race)
    _mark_signal(wallet_id, token_symbol, tx_type)

    signal = await conn.fetchrow(
        """
        INSERT INTO copy_trade_signals
            (wallet_id, token_symbol, action, amount_usd, confidence_score,
             score_at_generation, status)
        VALUES ($1, $2, $3, $4, $5, $6, 'pending')
        ON CONFLICT (wallet_id, token_symbol, action, amount_usd) DO NOTHING
        RETURNING id, wallet_id, token_symbol, action, amount_usd,
                  confidence_score, status, created_at
        """,
        wallet_id, token_symbol, tx_type, round(amount_usd, 2), confidence,
        whale_score,
    )

    if signal:
        # ── Compute C_final = blend of C_tx and whale score W ────────
        c_tx = confidence
        c_final = round(0.5 * c_tx + 0.5 * whale_score, 2)

        # ── Build signal data for explanation ─────────────────────────
        signal_dict = dict(signal)
        signal_dict["is_receive"] = tx_type == "receive"
        signal_dict["wallet_label"] = wallet_label
        signal_dict["wallet_address"] = wallet_address
        signal_dict["confidence_final"] = c_final
        signal_dict["execution_rate_30d"] = execution_rate_30d

        # ── Generate explanation text ─────────────────────────────────
        explanation = generate_explanation(
            signal_data=signal_dict,
            whale_score=whale_score,
            median_amount_30d=median_amount_30d,
        )

        # ── Update signal row with explanation ───────────────────────
        await conn.execute(
            """
            UPDATE copy_trade_signals
            SET explanation = $2,
                explanation_stale = FALSE,
                score_at_generation = $3
            WHERE id = $1
            """,
            signal_dict["id"], explanation, whale_score,
        )

        signal_dict["explanation"] = explanation
        signal_dict["explanation_stale"] = False
        signal_dict["confidence_final"] = c_final
        signal_dict["score_at_generation"] = whale_score

        logger.info(
            "Signal generated: wallet=%s token=%s amount_usd=%s confidence=%s "
            "whale_score=%s c_final=%s explanation=%s",
            wallet_id, token_symbol, round(amount_usd, 2), confidence,
            whale_score, c_final,
            explanation[:60] + "..." if len(explanation) > 60 else explanation,
        )
        return signal_dict
    return None
