"""
Alert evaluation service.
Called after monitor detects balance/transaction changes.
Evaluates user alert rules against the new state and fires matching alerts.

Rule types supported:
- large_transaction: a single tx exceeds threshold USD
- whale_buy: a whale wallet made a buy/receive tx above threshold USD
- portfolio_change: portfolio total changed by more than threshold %
- balance_drop: any personal wallet's balance dropped by threshold % from prev
"""
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("chainwatch.alerts")

SUPPORTED_RULE_TYPES = {"large_transaction", "whale_buy", "portfolio_change", "balance_drop"}

# In-memory cooldown cache: alert_id → last_fired_timestamp
# Prevents re-firing the same alert within the cooldown window (Finding: no cooldown = alert spam)
_cooldown_cache: dict = {}
_COOLDOWN_SECONDS = 300  # 5-minute cooldown per alert
_COOLDOWN_PRUNE_INTERVAL = 600  # prune every 10 minutes
_last_cooldown_prune: float = 0.0


async def _send_tg_safe(chat_id: str, message: str) -> None:
    """Send a Telegram alert, swallowing all errors (fire-and-forget)."""
    try:
        from services.telegram_alerts import send_telegram_alert
        await send_telegram_alert(message, chat_id=chat_id)
    except Exception:
        pass  # Never let Telegram failures affect alerting


def _prune_cooldown_cache() -> None:
    """Remove expired cooldown entries to prevent unbounded memory growth (Pitfall #12)."""
    import time
    global _last_cooldown_prune
    now = time.time()
    if now - _last_cooldown_prune < _COOLDOWN_PRUNE_INTERVAL:
        return
    _last_cooldown_prune = now
    expired = [k for k, v in _cooldown_cache.items() if now - v > _COOLDOWN_SECONDS * 2]
    for k in expired:
        del _cooldown_cache[k]


def _is_cooldown_active(alert_id: str) -> bool:
    """Check if alert is still in cooldown window."""
    import time
    _prune_cooldown_cache()  # Prune on read to bound memory (Pitfall #12: unbounded state dicts)
    last_fired = _cooldown_cache.get(alert_id)
    if last_fired and (time.time() - last_fired) < _COOLDOWN_SECONDS:
        return True
    return False


def _mark_cooldown(alert_id: str) -> None:
    """Mark alert as just fired."""
    import time
    _cooldown_cache[alert_id] = time.time()


async def evaluate_alerts(
    conn,
    changed_wallets: list,
    prev_balance_map: dict,
) -> list:
    """
    Evaluate alerts for users who own wallets in the changed set.

    Args:
        conn: active asyncpg connection (caller manages transaction)
        changed_wallets: list of (wid, addr, label, chain, is_whale, is_mine, user_id, result)
                         where result = (bal_native, bal_usd, tx_hash, tx_type, token, tx_amount_native)
        prev_balance_map: dict of wid → prev_balance_usd (captured before Phase 5 update)

    Returns:
        List of fired alert dicts (each with alert_id, user_id, rule_type,
        threshold, trigger_value, message)
    """
    if not changed_wallets:
        return []

    # Collect distinct user_ids
    changed_user_ids = list({uid for _, _, _, _, _, _, uid, _ in changed_wallets})

    rows = await conn.fetch(
        """
        SELECT a.id, a.user_id, a.rule_type, a.threshold, a.last_fired_at,
               a.notify_telegram
        FROM alerts a
        WHERE a.enabled = TRUE
          AND a.user_id = ANY($1)
        ORDER BY a.created_at DESC
        """,
        changed_user_ids,
    )

    if not rows:
        return []

    # ── Pre-batch tx usd_values for all changed wallets with tx_hashes ───
    # This avoids N+1 per-tx SELECT inside the rule evaluation loop (Pitfall #16).
    _tx_usd_cache: dict = {}  # (wallet_id, tx_hash) → usd_value
    tx_lookup_pairs: list = []
    for wid, _, _, _, _, _, _, result in changed_wallets:
        tx_hash = result[2]  # result = (bal_native, bal_usd, tx_hash, tx_type, token, tx_amount_native)
        if tx_hash:
            tx_lookup_pairs.append((wid, tx_hash))
    if tx_lookup_pairs:
        wallet_ids = [p[0] for p in tx_lookup_pairs]
        tx_hashes = [p[1] for p in tx_lookup_pairs]
        try:
            tx_rows = await conn.fetch(
                """
                SELECT wallet_id, tx_hash, usd_value
                FROM transactions
                WHERE wallet_id = ANY($1) AND tx_hash = ANY($2)
                """,
                wallet_ids,
                tx_hashes,
            )
            for tr in tx_rows:
                key = (str(tr["wallet_id"]), tr["tx_hash"])
                _tx_usd_cache[key] = float(tr["usd_value"] or 0)
        except Exception as e:
            logger.warning("Batch tx lookup failed, falling back to per-tx queries: %s", e)
            _tx_usd_cache = {}  # Will trigger fallback in individual lookups

    fired = []
    for alert in rows:
        alert_id = str(alert["id"])

        # Finding 10: Cooldown enforcement — skip if alert was recently fired
        # Check both DB-level last_fired_at and in-memory cache for speed
        last_fired_db = alert["last_fired_at"]
        if last_fired_db and (datetime.now(timezone.utc) - last_fired_db).total_seconds() < _COOLDOWN_SECONDS:
            continue
        if _is_cooldown_active(alert_id):
            continue

        rule_type = alert["rule_type"]
        threshold = float(alert["threshold"] or 0)
        user_id = alert["user_id"]

        if rule_type not in SUPPORTED_RULE_TYPES:
            logger.warning("Unknown alert rule_type=%s for alert %s", rule_type, alert_id)
            continue

        try:
            if rule_type == "large_transaction":
                # Check if any changed wallet for this user has a tx above threshold
                for wid, addr, _label, chain, is_whale, is_mine_flag, uid, result in changed_wallets:
                    if str(uid) != str(user_id):
                        continue
                    tx_hash = result[2]
                    tx_type = result[3]
                    token = result[4]
                    if tx_hash and tx_type in ("buy", "receive", "send"):
                        # Use pre-batched cache (Pitfall #16 fix: no per-tx SELECT)
                        tx_usd = _tx_usd_cache.get((str(wid), tx_hash))
                        if tx_usd is None:
                            # Fallback: single query if batch missed (cache not populated)
                            try:
                                tx_row = await conn.fetchrow(
                                    "SELECT usd_value FROM transactions WHERE wallet_id = $1 AND tx_hash = $2 ORDER BY created_at DESC LIMIT 1",
                                    wid, tx_hash,
                                )
                                tx_usd = float(tx_row["usd_value"] or 0) if tx_row else 0.0
                            except Exception:
                                tx_usd = 0.0
                        if tx_usd >= threshold:
                            fired.append({
                                "alert_id": alert_id,
                                "user_id": str(user_id),
                                "rule_type": rule_type,
                                "threshold": threshold,
                                "trigger_value": tx_usd,
                                "message": f"Large transaction: {token} ${tx_usd:,.0f}",
                                "notify_telegram": alert.get("notify_telegram", True),
                            })
                            break  # One fire per alert per cycle

            elif rule_type == "whale_buy":
                # Check if any whale wallet for this user made a buy above threshold
                for wid, addr, _label, chain, is_whale, is_mine_flag, uid, result in changed_wallets:
                    if str(uid) != str(user_id) or not is_whale:
                        continue
                    tx_hash = result[2]
                    tx_type = result[3]
                    token = result[4]
                    if tx_hash and tx_type in ("buy", "receive"):
                        # Use pre-batched cache (Pitfall #16 fix: no per-tx SELECT)
                        tx_usd = _tx_usd_cache.get((str(wid), tx_hash))
                        if tx_usd is None:
                            # Fallback: single query if batch missed
                            try:
                                tx_row = await conn.fetchrow(
                                    "SELECT usd_value FROM transactions WHERE wallet_id = $1 AND tx_hash = $2 ORDER BY created_at DESC LIMIT 1",
                                    wid, tx_hash,
                                )
                                tx_usd = float(tx_row["usd_value"] or 0) if tx_row else 0.0
                            except Exception:
                                tx_usd = 0.0
                        if tx_usd >= threshold:
                            fired.append({
                                "alert_id": alert_id,
                                "user_id": str(user_id),
                                "rule_type": rule_type,
                                "threshold": threshold,
                                "trigger_value": tx_usd,
                                "message": f"Whale buy: {token} ${tx_usd:,.0f}",
                                "notify_telegram": alert.get("notify_telegram", True),
                            })
                            break

            elif rule_type == "portfolio_change":
                # Check portfolio total change percentage for this user
                total_row = await conn.fetchrow(
                    """
                    SELECT SUM(balance_usd) as total_usd
                    FROM wallets
                    WHERE user_id = $1 AND is_mine = TRUE AND is_whale = FALSE
                    """,
                    user_id,
                )
                current_total = float(total_row["total_usd"] or 0) if total_row else 0.0

                # Compute prev_total via delta method:
                # current_total - sum(deltas for changed wallets) = prev_total
                # delta = current_balance_usd - prev_balance_usd for each changed wallet
                # This avoids a redundant DB query for prev_total_row (Pitfall #16).
                delta_sum = sum(
                    (result[1] - prev_balance_map.get(wid, result[1]))  # delta = current - prev
                    for wid, _, _, _, is_whale_flag, is_mine_flag, uid, result in changed_wallets
                    if str(uid) == str(user_id) and is_mine_flag and not is_whale_flag
                )
                prev_total = current_total - delta_sum

                if prev_total > 0:
                    pct_change = abs(current_total - prev_total) / prev_total * 100
                    if pct_change >= threshold:
                        fired.append({
                            "alert_id": alert_id,
                            "user_id": str(user_id),
                            "rule_type": rule_type,
                            "threshold": threshold,
                            "trigger_value": round(pct_change, 2),
                            "message": f"Portfolio changed {pct_change:.1f}% (threshold: {threshold}%)",
                            "notify_telegram": alert.get("notify_telegram", True),
                        })

            elif rule_type == "balance_drop":
                # Check if any personal (owned) wallet's balance dropped by threshold %
                for wid, addr, _label, chain, is_whale, is_mine_flag, uid, result in changed_wallets:
                    # Finding 12 FIX: skip non-owned wallets (only alert on is_mine wallets)
                    if str(uid) != str(user_id) or is_whale or not is_mine_flag:
                        continue
                    # Additional check: ensure this is a personal wallet (is_mine=True)
                    # We rely on the changed_wallets data here; the is_mine flag is passed in the tuple
                    bal_usd = result[1]
                    prev_usd = prev_balance_map.get(wid, 0.0)
                    if prev_usd > 0:
                        drop_pct = (prev_usd - bal_usd) / prev_usd * 100
                        if drop_pct >= threshold:
                            fired.append({
                                "alert_id": alert_id,
                                "user_id": str(user_id),
                                "rule_type": rule_type,
                                "threshold": threshold,
                                "trigger_value": round(drop_pct, 2),
                                "message": f"Balance dropped {drop_pct:.1f}% (threshold: {threshold}%)",
                                "notify_telegram": alert.get("notify_telegram", True),
                            })
                            break

        except Exception as e:
            logger.warning(
                "Alert eval error: alert=%s rule=%s error=%s",
                alert_id, rule_type, e,
            )
            continue

    # Persist fired alerts and update cooldowns
    # Batch INSERT all fired alerts, then batch UPDATE last_fired_at (Pitfall #16).
    persisted = []
    # Telegram chat ID map: user_id → chat_id (populated only when alerts fire)
    tg_chat_map: dict = {}
    if fired:
        # ── Fetch Telegram chat IDs for users with alerts firing ─────────
        # Batch fetch to avoid N+1 queries (Pitfall #16).
        tg_user_ids = list({f["user_id"] for f in fired})
        tg_chat_map: dict = {}
        try:
            telegram_rows = await conn.fetch(
                "SELECT id, telegram_chat_id FROM users WHERE id = ANY($1) AND telegram_chat_id IS NOT NULL",
                tg_user_ids,
            )
            tg_chat_map = {str(r["id"]): r["telegram_chat_id"] for r in telegram_rows}
        except Exception as e:
            logger.warning("Failed to fetch Telegram chat IDs: %s", e)

        # Batch INSERT fired_alerts
        fa_alert_ids = [f["alert_id"] for f in fired]
        fa_user_ids = [f["user_id"] for f in fired]
        fa_rule_types = [f["rule_type"] for f in fired]
        fa_trigger_values = [f["trigger_value"] for f in fired]
        fa_details = ["{}"] * len(fired)
        fa_messages = [f.get("message", "") for f in fired]
        try:
            await conn.execute(
                """
                INSERT INTO fired_alerts
                    (alert_id, user_id, rule_type, trigger_value, details, message)
                SELECT
                    unnest($1::uuid[]), unnest($2::uuid[]), unnest($3::text[]),
                    unnest($4::numeric[]), unnest($5::jsonb[]), unnest($6::text[])
                ON CONFLICT (alert_id, trigger_value) DO NOTHING
                """,
                fa_alert_ids, fa_user_ids, fa_rule_types,
                fa_trigger_values, fa_details, fa_messages,
            )
        except Exception as e:
            logger.warning("Batch INSERT fired_alerts failed: %s, falling back to per-row", e)
            # Fallback: per-row insert (also handles pre-006 schema without 'message' column)
            for f in fired:
                try:
                    # Try with 'message' column first (post-006 schema)
                    await conn.execute(
                        """
                        INSERT INTO fired_alerts
                            (alert_id, user_id, rule_type, trigger_value, details, message)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (alert_id, trigger_value) DO NOTHING
                        """,
                        f["alert_id"], f["user_id"], f["rule_type"],
                        f["trigger_value"], "{}", f.get("message", ""),
                    )
                except Exception:
                    # Pre-006 schema fallback: omit 'message' column
                    try:
                        await conn.execute(
                            """
                            INSERT INTO fired_alerts
                                (alert_id, user_id, rule_type, trigger_value, details)
                            VALUES ($1, $2, $3, $4, $5)
                            ON CONFLICT (alert_id, trigger_value) DO NOTHING
                            """,
                            f["alert_id"], f["user_id"], f["rule_type"],
                            f["trigger_value"], "{}",
                        )
                    except Exception:
                        pass

        # Batch UPDATE last_fired_at for all fired alert IDs
        try:
            await conn.execute(
                "UPDATE alerts SET last_fired_at = NOW() WHERE id = ANY($1)",
                fa_alert_ids,
            )
        except Exception as e:
            logger.warning("Batch UPDATE alerts.last_fired_at failed: %s", e)

    # Mark cooldowns and build persisted list
    for f in fired:
        _mark_cooldown(f["alert_id"])
        persisted.append(f)
        logger.info(
            "Alert fired: alert=%s user=%s rule=%s",
            f["alert_id"], f["user_id"], f["rule_type"],
        )

    # ── Send Telegram notifications (outside DB connection scope) ───────
    # Pitfall #7: Telegram HTTP calls must NOT be made while holding a DB
    # connection. The DB conn is released by the caller after this function
    # returns, so we schedule Telegram sends as fire-and-forget tasks.
    if tg_chat_map:
        import asyncio as _asyncio_mod

        for f in fired:
            # Per-alert opt-in: skip if this alert has notify_telegram = FALSE
            if not f.get("notify_telegram", True):
                continue
            chat_id = tg_chat_map.get(str(f["user_id"]))
            if not chat_id:
                continue
            msg = f.get("message", f"Alert: {f['rule_type']} triggered")
            formatted = (
                f"🔔 <b>ChainWatch Alert</b>\n\n{msg}\n\n"
                f"Rule: {f['rule_type']}\n"
                f"Threshold: {f['threshold']}\n"
                f"Value: {f['trigger_value']}"
            )
            _asyncio_mod.create_task(_send_tg_safe(chat_id, formatted))

    return persisted
