"""
Whale wallet scoring service.
Computes a composite whale score W (0.0-1.0) per wallet every monitor cycle.

Score formula:
  W = 0.15*S_activity + 0.25*S_reliability + 0.30*S_weight + 0.15*S_recency + 0.15*S_diversity

Cold-start blend (when signal count is low):
  prior = 0.5*min(log10(max(balance_usd, 1))/8, 1.0) + 0.5*min(wallet_age_days/365, 1.0)
  λ = 1.0 / (1.0 + signal_count_90d)
  effective_score = λ*prior + (1-λ)*W
"""
import logging
import math
import time as _time

logger = logging.getLogger("chainwatch.whale_scorer")

# ── Query timing threshold ────────────────────────────────────────────
# If the scoring query exceeds this duration (ms), emit a WARNING so
# the operator can investigate before it causes monitor cycle overruns.
SCORE_QUERY_SLOW_THRESHOLD_MS = 500


async def score_whale_wallet(conn, wallet_id: str) -> dict:
    """
    Compute the whale score for a single wallet.

    Args:
        conn: active asyncpg connection (caller manages transaction)
        wallet_id: UUID of the wallet to score

    Returns:
        dict with keys:
            score, score_activity, score_reliability, score_weight,
            score_recency, score_diversity, score_signals_used,
            score_is_coldstart, median_amount_30d, execution_rate_30d
    """

    # ── Fetch all features in a single query ──────────────────────────
    _t0 = _time.monotonic()
    row = await conn.fetchrow(
        """
        SELECT
            -- F1: signal count 30d
            COUNT(*) FILTER (WHERE cts.created_at >= NOW() - INTERVAL '30 days')
                AS signal_count_30d,
            -- F2: signal count 90d
            COUNT(*) FILTER (WHERE cts.created_at >= NOW() - INTERVAL '90 days')
                AS signal_count_90d,
            -- F3: execution rate 30d
            COALESCE(
                COUNT(*) FILTER (
                    WHERE cts.status = 'executed'
                    AND cts.created_at >= NOW() - INTERVAL '30 days'
                )::DECIMAL
                / NULLIF(COUNT(*) FILTER (
                    WHERE cts.created_at >= NOW() - INTERVAL '30 days'
                ), 0),
                0
            ) AS execution_rate_30d,
            -- F4: execution rate 90d
            COALESCE(
                COUNT(*) FILTER (
                    WHERE cts.status = 'executed'
                    AND cts.created_at >= NOW() - INTERVAL '90 days'
                )::DECIMAL
                / NULLIF(COUNT(*) FILTER (
                    WHERE cts.created_at >= NOW() - INTERVAL '90 days'
                ), 0),
                0
            ) AS execution_rate_90d,
            -- F5: median signal amount 30d (wallet-level)
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY cts.amount_usd
            ) FILTER (WHERE cts.created_at >= NOW() - INTERVAL '30 days')
                AS median_signal_amount_30d,
            -- Global whale median fallback (all whale wallet signals in 30d)
            -- NOTE: This subquery runs once per wallet. For large whale sets,
            -- consider pre-computing global_median once per monitor cycle.
            (
                SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cts2.amount_usd)
                FROM copy_trade_signals cts2
                JOIN wallets w2 ON w2.id = cts2.wallet_id
                WHERE w2.is_whale = TRUE
                  AND cts2.created_at >= NOW() - INTERVAL '30 days'
            ) AS global_median_30d,
            -- F7: tokens traded 30d
            COUNT(DISTINCT cts.token_symbol) FILTER (
                WHERE cts.created_at >= NOW() - INTERVAL '30 days'
            ) AS tokens_traded_30d,
            -- F9: recency days (days since last signal in 90d, clamped to 90)
            EXTRACT(DAY FROM NOW() - MAX(cts.created_at) FILTER (
                WHERE cts.created_at >= NOW() - INTERVAL '90 days'
            ))::INT AS recency_days_raw,
            -- F10: wallet age days
            EXTRACT(DAY FROM NOW() - w.created_at)::INT AS wallet_age_days,
            -- F11: current balance
            w.balance_usd AS balance_usd_current,
            -- count of 30d signals (for median fallback threshold)
            COUNT(*) FILTER (
                WHERE cts.created_at >= NOW() - INTERVAL '30 days'
                    AND cts.amount_usd IS NOT NULL
            ) AS signal_count_30d_for_median
        FROM copy_trade_signals cts
        RIGHT JOIN wallets w ON w.id = cts.wallet_id
        WHERE w.id = $1
        GROUP BY w.id, w.created_at, w.balance_usd
        """,
        wallet_id,
    )
    _elapsed_ms = (_time.monotonic() - _t0) * 1000
    if _elapsed_ms > SCORE_QUERY_SLOW_THRESHOLD_MS:
        logger.warning(
            "score_whale_wallet SLOW query: wallet=%s elapsed=%.1fms (threshold=%dms)",
            wallet_id, _elapsed_ms, SCORE_QUERY_SLOW_THRESHOLD_MS,
        )
    else:
        logger.debug(
            "score_whale_wallet query: wallet=%s elapsed=%.1fms",
            wallet_id, _elapsed_ms,
        )

    # Wallet not found
    if row is None:
        logger.warning("score_whale_wallet: wallet %s not found", wallet_id)
        return {
            "score": 0.0,
            "score_activity": 0.0,
            "score_reliability": 0.0,
            "score_weight": 0.0,
            "score_recency": 0.0,
            "score_diversity": 0.0,
            "score_signals_used": 0,
            "score_is_coldstart": True,
            "median_amount_30d": 0.0,
            "execution_rate_30d": 0.0,
        }

    # Extract features
    signal_count_30d = row["signal_count_30d"] or 0
    signal_count_90d = row["signal_count_90d"] or 0
    execution_rate_30d = float(row["execution_rate_30d"] or 0)
    execution_rate_90d = float(row["execution_rate_90d"] or 0)
    median_30d_raw = row["median_signal_amount_30d"]
    global_median_30d_raw = row["global_median_30d"]
    signals_30d_for_median = row["signal_count_30d_for_median"] or 0
    tokens_traded_30d = row["tokens_traded_30d"] or 0
    recency_days_raw = row["recency_days_raw"]
    wallet_age_days = row["wallet_age_days"] or 0
    balance_usd = float(row["balance_usd_current"] or 0)

    # ── Resolve median amount (wallet-level or global fallback) ────────
    if signals_30d_for_median >= 3 and median_30d_raw is not None:
        median_amount_30d = float(median_30d_raw)
    elif global_median_30d_raw is not None:
        median_amount_30d = float(global_median_30d_raw)
    else:
        median_amount_30d = 0.0

    # ── S_activity ─────────────────────────────────────────────────────
    # log10(sc+1) / log10(101) where log10(101) ≈ 2.0043
    s_activity = min(math.log10(signal_count_30d + 1) / math.log10(101), 1.0)

    # ── S_reliability ──────────────────────────────────────────────────
    s_reliability = 0.7 * execution_rate_30d + 0.3 * execution_rate_90d

    # ── S_weight ───────────────────────────────────────────────────────
    # clamp((log10(max(median, 1000)) - 3) / 3, 0, 1)
    if median_amount_30d > 0:
        s_weight = (math.log10(max(median_amount_30d, 1000)) - 3) / 3
        s_weight = max(0.0, min(s_weight, 1.0))
    else:
        s_weight = 0.0

    # ── S_recency ──────────────────────────────────────────────────────
    # 1.0 - min(recency_days, 90) / 90.0
    if recency_days_raw is not None:
        recency_days = min(recency_days_raw, 90)
    else:
        recency_days = 90  # No signals → max staleness
    s_recency = 1.0 - recency_days / 90.0

    # ── S_diversity ────────────────────────────────────────────────────
    # clamp(tokens_traded_30d / signal_count_30d, 0, 1)
    if signal_count_30d > 0:
        token_diversity = tokens_traded_30d / signal_count_30d
    else:
        token_diversity = 0.0
    s_diversity = max(0.0, min(token_diversity, 1.0))

    # ── Composite W ────────────────────────────────────────────────────
    W = (
        0.15 * s_activity
        + 0.25 * s_reliability
        + 0.30 * s_weight
        + 0.15 * s_recency
        + 0.15 * s_diversity
    )

    # ── Cold-start blend ───────────────────────────────────────────────
    if signal_count_90d == 0:
        # Pure prior — no signals at all
        prior = (
            0.5 * min(math.log10(max(balance_usd, 1)) / 8, 1.0)
            + 0.5 * min(wallet_age_days / 365, 1.0)
        )
        effective_score = prior
        is_coldstart = True
    else:
        # Reliability-weighted blend: more signals → less prior influence
        prior = (
            0.5 * min(math.log10(max(balance_usd, 1)) / 8, 1.0)
            + 0.5 * min(wallet_age_days / 365, 1.0)
        )
        lam = 1.0 / (1.0 + signal_count_90d)
        effective_score = lam * prior + (1 - lam) * W
        # Mark as coldstart if we have signals but still very few
        is_coldstart = signal_count_90d < 3

    # Clamp
    effective_score = round(max(0.0, min(effective_score, 1.0)), 3)

    logger.debug(
        "Wallet %s scored: W=%.3f effective=%.3f activity=%.3f reliability=%.3f "
        "weight=%.3f recency=%.3f diversity=%.3f signals_90d=%d coldstart=%s",
        wallet_id, W, effective_score, s_activity, s_reliability,
        s_weight, s_recency, s_diversity, signal_count_90d, is_coldstart,
    )

    return {
        "score": effective_score,
        "score_activity": round(s_activity, 3),
        "score_reliability": round(s_reliability, 3),
        "score_weight": round(s_weight, 3),
        "score_recency": round(s_recency, 3),
        "score_diversity": round(s_diversity, 3),
        "score_signals_used": signal_count_90d,
        "score_is_coldstart": is_coldstart,
        "median_amount_30d": round(median_amount_30d, 2),
        "execution_rate_30d": round(execution_rate_30d, 3),
    }
