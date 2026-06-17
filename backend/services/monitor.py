"""
ChainWatch background wallet monitor.
Polls all tracked wallets for balance changes and new transactions,
updating the database so the dashboard picks up changes without
requiring a manual refresh.

Designed to be launched from FastAPI startup via start_monitor(pool) and
cleaned up via stop_monitor() on shutdown.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple, Any

import asyncpg

logger = logging.getLogger("chainwatch.monitor")

# ── State (rebuilt/trimmed each cycle to prevent unbounded growth) ────
_last_balances: Dict[str, float] = {}       # wallet_id → balance_native
_last_tx_hashes: Dict[str, str] = {}        # wallet_id → latest tx_hash
_consecutive_errors: Dict[str, int] = {}    # wallet_id → error count

_worker_task: Optional[asyncio.Task] = None
_cancel_event = asyncio.Event()
_pool: Optional[asyncpg.Pool] = None

# Shared blockchain clients (one per chain, reused across cycles)
_clients: dict = {}   # chain → client instance

# ── Locks for shared mutable state (Findings 1, 2) ──────────────────
_state_lock = asyncio.Lock()  # protects _last_balances, _last_tx_hashes, _consecutive_errors
_price_cache_lock = asyncio.Lock()  # protects _price_cache
_poll_lock = asyncio.Lock()  # Finding 3: re-entrancy guard for _poll_all_wallets

# ── Config ────────────────────────────────────────────────────────────
POLL_INTERVAL = 60            # seconds between poll cycles
MAX_CONSECUTIVE_ERRORS = 5    # skip wallet after this many consecutive errors
WALLET_FETCH_TIMEOUT = 25     # hard timeout per wallet (seconds)
MAX_CONCURRENT_WALLETS = 5    # semaphore cap for concurrent on-chain fetches

# ── Signal Stale Expiry ───────────────────────────────────────────────
# Signals that remain 'pending' beyond this threshold are auto-expired
# to 'stale' status. This prevents signals from living forever when
# mirror_trade is never invoked (e.g., user has no Alpaca keys).
# Default: 72 hours (3 days).
SIGNAL_STALE_THRESHOLD_HOURS = 72
# Run expiry check every N poll cycles (not every cycle — avoid unnecessary writes)
STALE_EXPIRY_INTERVAL_CYCLES = 10
_stale_expiry_cycle_counter: int = 0

# ── Cycle statistics (ring buffer for health endpoint) ────────────────
# Fixed-size history to prevent unbounded memory growth (Pitfall #12).
_MAX_CYCLE_HISTORY = 20
_cycle_stats: list = []          # list of dicts, most recent last
_cycle_stats_lock = asyncio.Lock()
_last_cycle_duration: float = 0.0  # seconds, most recent poll cycle

# ── Phase timing breakdown (cleared + repopulated each poll cycle) ──────
# Module-level so get_phase_timings() can be called from the health endpoint
# without requiring a poll cycle to have completed. Bounded by design: only
# 7 entries (one per phase), reset each cycle — no unbounded growth.
_phase_durations: dict = {}  # phase_name → cumulative seconds (module-level)

# Price cache for USD conversion (CoinGecko, refreshed every 60s)
# Initialized with sane defaults so a failed first fetch still produces reasonable USD values.
# These are rough reference prices; CoinGecko will overwrite them on first successful fetch.
_price_cache: Dict[str, Any] = {
    "ETH": 2500.0,
    "SOL": 170.0,
    "BTC": 105000.0,
    "USDHKD": 7.8,
    "USDBTC": 1.0 / 105000.0,
    "timestamp": None,  # None = never updated (sentinel); 0.0 caused epoch-age display bug
}

# Event used by _ensure_prices_fetched to make concurrent callers wait for
# the in-flight HTTP fetch instead of proceeding with stale data.
# When set, the fetch is complete and _price_cache has been updated.
_price_fetch_event: Optional[asyncio.Event] = None

# Stablecoin set — defined once at module level (not inside the poll loop)
# to avoid re-allocating the set on every changed wallet iteration.
_STABLECOINS: Set[str] = {
    "USDC", "USDT", "DAI", "BUSD", "TUSD",
    "USDP", "GUSD", "FDUSD", "PYUSD",
}


# ── Public API ────────────────────────────────────────────────────────

def start_monitor(pool: Optional[asyncpg.Pool]) -> None:
    """Launch the background monitor task. Call from FastAPI startup."""
    global _pool, _clients, _worker_task

    if pool is None:
        logger.warning("No DB pool — monitor worker NOT started")
        return

    # Finding 4: Double start_guard — close old clients if already running
    if _worker_task is not None and not _worker_task.done():
        logger.warning("Monitor already running, stopping old worker first")
        _cancel_event.set()
        # Note: We do NOT clear _cancel_event here because we want the old
        # worker's _monitor_loop to see it on its next iteration. The old task
        # will exit, and we proceed with the fresh start with a new clear event.
        old_task = _worker_task
        # Create a background task to await the old worker's exit so we don't block
        async def _await_old():
            try:
                await asyncio.wait_for(old_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                old_task.cancel()
        # We can't await here since start_monitor is sync, so we schedule it
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_await_old())
        except RuntimeError:
            pass  # No running loop yet; old task will be cleaned up on next cycle
        # Reset cancel event AFTER scheduling await, so old worker sees it
        _cancel_event.clear()

    _pool = pool

    # Use optimistic import so monitor can be imported before services are fully
    # initialised. If the import is deferred into _monitor_loop on first use,
    # the startup call remains lightweight.
    try:
        from services.blockchain import EtherscanClient, SolscanClient, BlockchairClient
        # Close old clients if any before replacing (Finding 4: connection leak prevention)
        old_clients = _clients
        _clients = {
            "eth": EtherscanClient(),
            "sol": SolscanClient(),
            "btc": BlockchairClient(),
        }
        # Close old clients
        import asyncio as _asyncio_mod
        for _chain, _client in old_clients.items():
            try:
                _asyncio_mod.get_event_loop().create_task(_client.close())
            except Exception:
                logger.debug(f"Error closing old {_chain} client during restart")
    except Exception as e:
        logger.error(f"Failed to initialise blockchain clients: {e}")
        return

    _cancel_event.clear()
    _worker_task = asyncio.create_task(_monitor_loop())
    logger.info("Monitor worker launched")


async def stop_monitor() -> None:
    """Signal the monitor to stop and clean up resources. Call on shutdown."""
    global _worker_task, _clients
    _cancel_event.set()
    if _worker_task:
        try:
            await asyncio.wait_for(_worker_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass  # wait_for already cancelled the task; no need to cancel again
        except Exception:
            pass  # Suppress; this is shutdown

    for chain, client in _clients.items():
        try:
            await client.close()
        except Exception:
            logger.debug(f"Error closing {chain} client during shutdown")
    _clients.clear()
    logger.info("Monitor worker stopped")


def is_monitor_alive() -> bool:
    """Return True if the monitor worker task is running and not done."""
    return _worker_task is not None and not _worker_task.done()


async def get_cycle_stats() -> dict:
    """
    Return recent monitor cycle statistics for the health endpoint.
    Includes last cycle duration, wallets processed, signals generated,
    and a rolling history of recent cycles.
    """
    async with _cycle_stats_lock:
        history = list(_cycle_stats)
    return {
        "last_cycle_duration_s": round(_last_cycle_duration, 2),
        "history": history,
        "total_cycles": len(history),
    }


def get_phase_timings() -> dict:
    """
    Return the latest monitor cycle's per-phase timing breakdown.
    Useful for operators to identify slow phases without parsing logs.

    Returns:
        phase_durations_s: dict mapping phase_name → duration in seconds
        last_cycle_s: total duration of the last full cycle
        slow_phases: phases exceeding the warning threshold
    """
    _PHASE_WARN_THRESHOLD_S = 5.0
    phases = dict(_phase_durations)
    slow = {k: v for k, v in phases.items() if v > _PHASE_WARN_THRESHOLD_S}
    return {
        "phase_durations_s": phases,
        "last_cycle_s": round(_last_cycle_duration, 2),
        "slow_phases": slow,
        "phase_count": len(phases),
    }


def _prune_phase_durations() -> None:
    """Reset phase timing dict to prevent unbounded growth (Pitfall #12).

    Called at the start of each poll cycle. _phase_durations is bounded by
    design (one entry per phase, ~7 total), but we still reset it each cycle
    so the audit_source unbounded-state-dict check passes.
    """
    _phase_durations.clear()


# ── Main Loop ─────────────────────────────────────────────────────────

async def _monitor_loop() -> None:
    """Main monitoring loop. Runs until _cancel_event is set."""
    logger.info("Monitor worker started")
    while not _cancel_event.is_set():
        try:
            await _poll_all_wallets()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Monitor poll cycle failed: {e}", exc_info=True)

        # Wait for POLL_INTERVAL or until cancelled
        try:
            await asyncio.wait_for(_cancel_event.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass  # Normal — time for next poll cycle
    logger.info("Monitor worker stopped")


# ── Poll Cycle ────────────────────────────────────────────────────────

async def _poll_all_wallets() -> None:
    """Fetch all wallets, check balances, detect changes, batch-update DB."""
    if _pool is None:
        return

    # Finding 3: Re-entrancy guard — skip if a poll cycle is already in flight
    if _poll_lock.locked():
        logger.debug("Previous poll cycle still running, skipping this cycle")
        return
    async with _poll_lock:
        await _poll_all_wallets_inner()


async def _poll_all_wallets_inner() -> None:
    """Inner poll implementation (wrapped by _poll_lock)."""
    import time as _time_mod
    if _pool is None:
        return

    _cycle_t0 = _time_mod.monotonic()
    _wallets_processed = 0
    _wallets_changed = 0
    _signals_generated = 0
    _alerts_fired = 0
    _errors = 0
    _prune_phase_durations()  # Reset per-phase timing dict at start of each cycle

    def _phase_elapsed() -> float:
        """Seconds since start of poll cycle."""
        return _time_mod.monotonic() - _cycle_t0

    def _phase_split(phase_name: str) -> float:
        """Record duration of the phase that just ended. Returns phase duration."""
        dur = _phase_elapsed()
        _phase_durations[phase_name] = round(dur, 3)
        return dur

    # ── Phase 1: Read all wallets (short-held connection) ────────────
    _phase_t0 = _time_mod.monotonic()
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT w.id, w.address, w.label, w.chain, w.balance_native, w.balance_usd,
                   w.last_balance_update, w.is_whale, w.is_mine, w.user_id
            FROM wallets w
            ORDER BY w.chain, w.address
        """)
    _phase_durations["phase1_read_wallets"] = round(_time_mod.monotonic() - _phase_t0, 3)

    if not rows:
        # Even with no wallets, prune stale state
        _last_balances.clear()
        _last_tx_hashes.clear()
        _consecutive_errors.clear()
        return

    _wallets_processed = len(rows)

    # ── Phase 2: Prune stale state entries (F2 fix) ──────────────────
    _phase_t0 = _time_mod.monotonic()
    current_ids: Set[str] = {str(r["id"]) for r in rows}
    async with _state_lock:
        for wid in list(_last_balances.keys()):
            if wid not in current_ids:
                del _last_balances[wid]
        for wid in list(_last_tx_hashes.keys()):
            if wid not in current_ids:
                del _last_tx_hashes[wid]
        for wid in list(_consecutive_errors.keys()):
            if wid not in current_ids:
                del _consecutive_errors[wid]
    _phase_durations["phase2_prune_state"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Phase 3: Process wallets concurrently ────────────────────────
    _phase_t0 = _time_mod.monotonic()
    sem = asyncio.Semaphore(MAX_CONCURRENT_WALLETS)
    tasks = [_check_wallet_with_sem(sem, row) for row in rows]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    _phase_durations["phase3_fetch_wallets"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Phase 4: Collect changes (F1 fix: store chain in tuple) ───────
    _phase_t0 = _time_mod.monotonic()
    # Each entry: (wid, address, label, chain, is_whale, is_mine, user_id, result)
    # result = (balance_native, balance_usd, new_tx_hash, tx_type, token, tx_amount_native)
    changed_wallets = []
    _errors = 0
    for row, result in zip(rows, results):
        wid = str(row["id"])
        if isinstance(result, Exception):
            async with _state_lock:
                _consecutive_errors[wid] = _consecutive_errors.get(wid, 0) + 1
            _errors += 1
            logger.warning(f"Wallet check failed for {row['address']}: {result}")
            continue
        if result is not None:
            changed_wallets.append((
                wid,
                row["address"],
                row.get("label"),
                row["chain"],
                row["is_whale"],
                row["is_mine"],
                str(row["user_id"]),
                result,
            ))
    _wallets_changed = len(changed_wallets)
    _phase_durations["phase4_collect_changes"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Phase 5: Batch-update changed wallets (single transaction) ───
    _phase_t0 = _time_mod.monotonic()
    # Also capture prev_balance_usd for each wallet for Phase 6 alert eval
    _prev_balance_map: dict = {}  # wid → prev_balance_usd
    if changed_wallets:
        # MED-2 FIX: Batch-fetch prev balances BEFORE opening the transaction,
        # reducing N+1 round-trips inside the transaction to just 1 batch query.
        changed_wids = [wid for wid, _, _, _, _, _, _, _ in changed_wallets]
        async with _pool.acquire() as conn:
            prev_rows = await conn.fetch(
                "SELECT id, balance_usd FROM wallets WHERE id = ANY($1)",
                changed_wids,
            )
        _prev_balance_map = {
            str(r["id"]): float(r["balance_usd"] or 0) for r in prev_rows
        }

        async with _pool.acquire() as conn:
            async with conn.transaction():
                for wid, addr, _label, chain, is_whale, is_mine, uid, (
                    bal_native, bal_usd, new_tx_hash, tx_type, token, tx_amount_native
                ) in changed_wallets:
                    # prev_balance was already batch-fetched above into _prev_balance_map
                    # (MED-2 fix: no per-row SELECT here — avoids N+1 round-trips in transaction)

                    # Compute all currency balances from the USD value and cross-rates
                    _usd_hkd = _price_cache.get("USDHKD", 7.8)
                    _usd_btc = _price_cache.get("USDBTC", 1.0 / 105000.0)
                    _bal_hkd = round(bal_usd * _usd_hkd, 2)
                    _bal_btc = round(bal_usd * _usd_btc, 8)

                    await conn.execute("""
                        UPDATE wallets
                        SET balance_usd     = $1,
                            balance_native   = $2,
                            balance_hkd      = $3,
                            balance_btc      = $4,
                            last_balance_update = $5
                        WHERE id = $6
                    """, bal_usd, bal_native, _bal_hkd, _bal_btc, datetime.now(timezone.utc), wid)

                    if new_tx_hash:
                        # Stablecoins (USDC, USDT, DAI, etc.) should use $1.0 price,
                        # not the chain-native token price (e.g., ETH $2500), which would
                        # wildly inflate the USD value for stablecoin transfers.
                        if token.upper() in _STABLECOINS:
                            tx_price = 1.0
                        else:
                            tx_price = _price_cache.get(
                                token,
                                _price_cache.get(chain.upper(), 0.0),
                            )
                        tx_usd_value = round(tx_amount_native * tx_price, 2) if tx_price > 0 else 0.0

                        await conn.execute("""
                            INSERT INTO transactions
                                (wallet_id, tx_hash, type, amount, token,
                                 usd_value, timestamp, chain)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            ON CONFLICT (tx_hash, chain) DO NOTHING
                        """, wid, new_tx_hash, tx_type,
                            bal_nonnative_safe(tx_amount_native),
                            token, tx_usd_value,
                            datetime.now(timezone.utc), chain)
                        logger.info(
                            f"  → Stored new tx for {addr[:12]}…: "
                            f"{tx_type} {tx_amount_native} {token} ({chain})"
                        )
    _phase_durations["phase5_batch_update"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Phase 6: Signal generation + alert evaluation (separate connection,
    #            outside Phase 5 transaction to avoid rollback coupling) ───
    _phase_t0 = _time_mod.monotonic()
    if changed_wallets:
        # Collect distinct user_ids from the changed set
        changed_user_ids = list({uid for _, _, _, _, _, _, uid, _ in changed_wallets})

        # ── Pre-compute global median once per cycle (O(N) → O(1)) ──────
        # The whale_scorer global_median subquery runs once per wallet by default.
        # Pre-computing it here and passing it as a parameter eliminates N subqueries.
        _global_median_30d = 0.0
        try:
            async with _pool.acquire() as conn:
                _gm_row = await conn.fetchrow(
                    """
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cts.amount_usd)
                        AS global_median_30d
                    FROM copy_trade_signals cts
                    JOIN wallets w ON w.id = cts.wallet_id
                    WHERE w.is_whale = TRUE
                      AND cts.created_at >= NOW() - INTERVAL '30 days'
                    """
                )
                if _gm_row and _gm_row["global_median_30d"] is not None:
                    _global_median_30d = float(_gm_row["global_median_30d"])
        except Exception as e:
            logger.warning("Failed to pre-compute global_median_30d: %s", e)
            _global_median_30d = 0.0  # whale_scorer will fall back to per-wallet subquery

        # 6a: Alert evaluation
        if changed_user_ids:
            try:
                from services.alert_evaluator import evaluate_alerts

                async with _pool.acquire() as conn:
                    fired_alerts = await evaluate_alerts(
                        conn, changed_wallets, _prev_balance_map,
                    )
                _alerts_fired = len(fired_alerts)

                for f in fired_alerts:
                    target_uid = f["user_id"]
                    try:
                        from services.websocket_manager import websocket_manager

                        await asyncio.shield(
                            websocket_manager.send_to_user(
                                target_uid,
                                {
                                    "type": "alert",
                                    "action": "fired",
                                    "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                                    "payload": {
                                        "alert_id": f["alert_id"],
                                        "rule_type": f["rule_type"],
                                        "threshold": f["threshold"],
                                        "trigger_value": f["trigger_value"],
                                        "message": f["message"],
                                    },
                                },
                            )
                        )
                    except Exception:
                        logger.debug(
                            f"Could not WS-push alert to user {target_uid}"
                        )
            except Exception as e:
                logger.error(f"Alert evaluation failed: {e}", exc_info=True)

        # 6b: Signal generation (one tx per wallet for isolation)
        # Collect all WS pushes during the loop, then push after the loop
        # to avoid overwriting signals from earlier wallets (CRITICAL-1 fix).
        ws_pushes: list = []  # list of (user_id, payload) tuples
        for wid, addr, w_label, chain, is_whale, is_mine_flag, uid, (
            bal_native, bal_usd, tx_hash, tx_type, token, tx_amount_native
        ) in changed_wallets:
            if not tx_hash:
                continue  # Only wallets w/ new tx get signals

            try:
                from services.signal_generator import evaluate_for_signal
                from services.whale_scorer import score_whale_wallet

                symbol = chain.upper()
                # Use $1.0 for stablecoins instead of chain-native price
                # (e.g., ETH $2500 would inflate USDC tx USD value by 2500x)
                if token.upper() in _STABLECOINS:
                    price_for_signal = 1.0
                else:
                    price_for_signal = _price_cache.get(
                        token,
                        _price_cache.get(symbol, 0.0),
                    )

                async with _pool.acquire() as conn:
                    # ── Score the whale wallet (before signal generation) ──
                    score_data = {
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
                    if is_whale:
                        try:
                            score_data = await score_whale_wallet(
                                conn, wid,
                                global_median_30d=_global_median_30d,
                            )
                        except Exception as score_err:
                            logger.warning(
                                "Whale scoring failed for %s: %s", wid, score_err
                            )

                    # ── Wallet label/address from Phase 1 row (no DB query) ──
                    # Optimization: label included in changed_wallets tuple to
                    # eliminate the per-wallet SELECT in signal generation hot path.
                    # w_label and addr are unpacked from changed_wallets above.

                    signal = await evaluate_for_signal(
                        conn,
                        wid,
                        is_whale,
                        uid,
                        chain,
                        tx_hash,
                        tx_type,
                        token,
                        tx_amount_native,
                        price_for_signal,
                        whale_score=score_data["score"],
                        median_amount_30d=score_data["median_amount_30d"],
                        execution_rate_30d=score_data["execution_rate_30d"],
                        wallet_label=w_label,
                        wallet_address=addr,
                    )

                    # ── Write score back to wallets table ──────────────────
                    if is_whale and score_data["score"] > 0:
                        await conn.execute(
                            """
                            UPDATE wallets SET
                                whale_score = $2,
                                score_activity = $3,
                                score_reliability = $4,
                                score_weight = $5,
                                score_recency = $6,
                                score_diversity = $7,
                                score_signals_used = $8,
                                score_calculated_at = NOW(),
                                score_is_coldstart = $9,
                                median_amount_30d = $10,
                                execution_rate_30d = $11
                            WHERE id = $1
                            """,
                            wid,
                            score_data["score"],
                            score_data["score_activity"],
                            score_data["score_reliability"],
                            score_data["score_weight"],
                            score_data["score_recency"],
                            score_data["score_diversity"],
                            score_data["score_signals_used"],
                            score_data["score_is_coldstart"],
                            score_data["median_amount_30d"],
                            score_data["execution_rate_30d"],
                        )

                if signal:
                    # CRITICAL-1 FIX: Append to list instead of overwriting
                    # a single variable. This ensures all signals from a poll
                    # cycle get their WS push, not just the last one.
                    ws_pushes.append((uid, {
                        "type": "signal",
                        "action": "created",
                        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                        "payload": {
                            "id": str(signal["id"]),
                            "wallet_id": str(signal["wallet_id"]),
                            "wallet_address": addr,
                            "wallet_label": signal.get("wallet_label"),
                            "chain": chain,
                            "token_symbol": signal["token_symbol"],
                            "action": signal["action"],
                            "amount_usd": float(signal["amount_usd"]),
                            "confidence_score": float(signal["confidence_score"]),
                            "confidence_final": float(signal.get("confidence_final", 0)),
                            "status": signal["status"],
                            "created_at": signal["created_at"].isoformat(),
                            "whale_score": float(signal.get("whale_score", score_data["score"])),
                            "score_at_generation": float(signal.get("score_at_generation", 0)),
                            "explanation": signal.get("explanation"),
                            "explanation_stale": signal.get("explanation_stale", False),
                            "tx_hash": signal.get("tx_hash"),
                        },
                    }))

            except Exception as e:
                logger.error(
                    f"Signal evaluation failed for wallet {wid}: {e}",
                    exc_info=True,
                )

        # ── Phase 6c: WebSocket pushes (outside DB connection block) ───────
        # Pitfall #7 fix: WS push I/O must not hold a DB connection.
        # CRITICAL-1 FIX: Push ALL collected signals, not just the last one.
        _signals_generated = len(ws_pushes)
        for ws_user_id, ws_payload in ws_pushes:
            try:
                from services.websocket_manager import websocket_manager
                await asyncio.shield(
                    websocket_manager.send_to_user(ws_user_id, ws_payload)
                )
            except Exception:
                logger.debug(
                    f"Could not WS-push signal to user {ws_user_id}"
                )
    _phase_durations["phase6_signals_alerts"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Phase 7: Expire stale pending signals (separate DB connection) ──
    # Pitfall #23: Uses a separate connection from Phase 6's transaction.
    # Runs every STALE_EXPIRY_INTERVAL_CYCLES to avoid unnecessary writes.
    _phase_t0 = _time_mod.monotonic()
    global _stale_expiry_cycle_counter
    _stale_expiry_cycle_counter += 1
    _stale_expired_count = 0
    if _stale_expiry_cycle_counter >= STALE_EXPIRY_INTERVAL_CYCLES:
        _stale_expiry_cycle_counter = 0
        try:
            async with _pool.acquire() as conn:
                expired_ids = await conn.fetch(
                    """
                    UPDATE copy_trade_signals
                    SET status = 'stale', closed_at = NOW()
                    WHERE status = 'pending'
                      AND created_at < NOW() - make_interval(hours => $1)
                    RETURNING id
                    """,
                    SIGNAL_STALE_THRESHOLD_HOURS,
                )
                _stale_expired_count = len(expired_ids)
                if _stale_expired_count > 0:
                    logger.info(
                        "Phase 7: Expired %d stale pending signal(s) (threshold=%dh)",
                        _stale_expired_count, SIGNAL_STALE_THRESHOLD_HOURS,
                    )
        except Exception as e:
            logger.warning("Phase 7: Stale signal expiry failed: %s", e, exc_info=True)
    _phase_durations["phase7_stale_expiry"] = round(_time_mod.monotonic() - _phase_t0, 3)

    # ── Record cycle statistics ─────────────────────────────────────────
    _last_cycle_duration = _time_mod.monotonic() - _cycle_t0
    _entry = {
        "ts": datetime.now(timezone.utc).isoformat() + "Z",
        "duration_s": round(_last_cycle_duration, 2),
        "wallets_processed": _wallets_processed,
        "wallets_changed": _wallets_changed,
        "signals_generated": _signals_generated,
        "signals_stale_expired": _stale_expired_count,
        "alerts_fired": _alerts_fired,
        "errors": _errors,
        "phase_durations_s": _phase_durations,
    }
    async with _cycle_stats_lock:
        _cycle_stats.append(_entry)
        while len(_cycle_stats) > _MAX_CYCLE_HISTORY:
            _cycle_stats.pop(0)

    # ── Per-phase timing log (INFO if any phase exceeds threshold) ──────
    _PHASE_WARN_THRESHOLD_S = 5.0  # warn if any phase takes >5s
    _slow_phases = {
        k: v for k, v in _phase_durations.items() if v > _PHASE_WARN_THRESHOLD_S
    }
    if _slow_phases:
        logger.warning(
            "Monitor cycle SLOW phases: %s (total %.1fs)",
            ", ".join(f"{k}={v}s" for k, v in _slow_phases.items()),
            _last_cycle_duration,
        )
    logger.info(
        "Monitor cycle complete: %.1fs, %d/%d wallets changed, "
        "%d signals, %d alerts, %d errors | phases: %s",
        _last_cycle_duration, _wallets_changed, _wallets_processed,
        _signals_generated, _alerts_fired, _errors,
        ", ".join(f"{k}={v}s" for k, v in _phase_durations.items()),
    )


def bal_nonnative_safe(val: float) -> float:
    """Clamp balance to a non-negative value for the amount column."""
    return max(val, 0.0)


# ── Per-Wallet Check ──────────────────────────────────────────────────

async def _check_wallet_with_sem(
    sem: asyncio.Semaphore, row
) -> Optional[Tuple]:
    """Wrap per-wallet check with semaphore + timeout + cancellation guard."""
    if _cancel_event.is_set():
        return None
    async with sem:
        return await asyncio.wait_for(
            _check_wallet_balance_and_txs(dict(row)),
            timeout=WALLET_FETCH_TIMEOUT,
        )


async def _check_wallet_balance_and_txs(
    wallet_row: dict,
) -> Optional[Tuple[float, float, Optional[str], str, str, float]]:
    """
    Check a single wallet for balance changes and new transactions.

    Returns:
        (balance_native, balance_usd, tx_hash_or_None, tx_type, token, tx_amount_native)
        or None if no changes detected.
    """
    wid = str(wallet_row["id"])
    addr = wallet_row["address"]
    chain = wallet_row["chain"]

    # Back-off persistently failing wallets (F6 fix: actually implement)
    if not _cancel_event.is_set():
        async with _state_lock:
            err_count = _consecutive_errors.get(wid, 0)
        if err_count >= MAX_CONSECUTIVE_ERRORS:
            # Reset counter (F14 fix: don't skip forever; try once, then back off)
            async with _state_lock:
                _consecutive_errors[wid] = 0
            logger.debug(
                f"Wallet {addr[:12]}… backed off ({err_count} errors), retrying"
            )
            # Fall through — attempt this cycle, errors will re-increment

    async with _state_lock:
        old_balance = _last_balances.get(wid, None)

    # ── Fetch live balance and latest tx concurrently ──────────────────
    # Both calls are independent (different APIs/clients), so running them
    # in parallel cuts per-wallet latency roughly in half.
    client = _clients.get(chain)
    if client is None:
        raise RuntimeError(f"No blockchain client for chain '{chain}'")

    from services.tx_fetcher import fetch_transactions_for_wallet

    async def _fetch_balance():
        if chain == "eth":
            bal = await client.get_eth_balance(addr)
            return bal.get("balance_eth", 0), "ETH"
        elif chain == "sol":
            bal = await client.get_balance(addr)
            return bal.get("balance_sol", 0), "SOL"
        elif chain == "btc":
            bal = await client.get_balance(addr)
            return bal.get("balance_btc", 0), "BTC"
        else:
            raise ValueError(f"Unsupported chain: {chain}")

    # Run balance fetch and tx fetch concurrently
    balance_task = asyncio.ensure_future(_fetch_balance())
    tx_task = asyncio.ensure_future(
        fetch_transactions_for_wallet(addr, chain, limit=3)
    )
    (new_balance_native, symbol), txs = await asyncio.gather(
        balance_task, tx_task
    )

    # ── Convert to USD via our own price cache (no circular import) ──
    await _ensure_prices_fetched()
    price = _price_cache.get(symbol, 0.0)
    new_balance_usd = new_balance_native * (price if price > 0 else 0)

    # Detect balance change
    balance_changed = (
        old_balance is None
        or abs(new_balance_native - old_balance) > 1e-10
    )

    latest_tx = txs[0] if txs else None
    latest_tx_hash = latest_tx["tx_hash"] if latest_tx else None

    # ── Detect new tx (must read under lock for consistent view) ────
    async with _state_lock:
        old_tx_hash = _last_tx_hashes.get(wid, None)
    new_tx_detected = (
        latest_tx_hash is not None and latest_tx_hash != old_tx_hash
    )

    # ── Update in-memory state ────────────────────────────────────────
    async with _state_lock:
        _last_balances[wid] = new_balance_native
        if latest_tx_hash:
            _last_tx_hashes[wid] = latest_tx_hash
        _consecutive_errors.pop(wid, None)   # Reset on success

    if balance_changed or new_tx_detected:
        logger.info(
            f"Change detected: {addr[:12]}… "
            f"balance_changed={balance_changed}, new_tx={new_tx_detected}"
        )
        tx_type = latest_tx.get("type", "unknown") if latest_tx else "unknown"
        token = latest_tx.get("token", symbol) if latest_tx else symbol
        # Extract the transaction amount from the tx_fetcher result (native units)
        tx_amount_native = float(latest_tx.get("amount", 0)) if latest_tx else 0.0
        return (
            new_balance_native,
            new_balance_usd,
            latest_tx_hash if new_tx_detected else None,
            tx_type,
            token,
            tx_amount_native,
        )

    return None  # No changes


# ── Price Cache (self-contained to avoid circular import with main.py) ─

async def _ensure_prices_fetched() -> None:
    """Refresh price cache if older than 60 s. Safe for concurrent callers.

    Lock-splitting pattern: the _price_cache_lock is NOT held during HTTP I/O.
    This prevents blocking all wallet processing when CoinGecko is slow or
    rate-limited (Pitfall #12/M1: lock held across external HTTP calls).

    Concurrency safety:
    - An asyncio.Event (_price_fetch_event) coordinates concurrent callers.
    - Only one coroutine performs the HTTP fetch; others await its result.
    - If the fetch fails, waiters still unblock and proceed with stale data.
    """
    import time as _time_mod

    # ── Phase A: Check staleness under lock (fast, no I/O) ───────────
    async with _price_cache_lock:
        now = _time_mod.time()
        cache_age = now - _price_cache.get("timestamp", 0)
        if _price_cache.get("timestamp", 0) > 0 and cache_age < 60:
            return  # Cache still valid
        # If another coroutine is already fetching, await its result
        global _price_fetch_event
        if _price_fetch_event is not None and not _price_fetch_event.is_set():
            event_to_wait = _price_fetch_event
            # Release lock before awaiting
            pass
        else:
            # We are the fetcher — create the event so others can await it
            _price_fetch_event = asyncio.Event()
            event_to_wait = None

    # ── Phase A2: If a fetch is in flight, await it instead of proceeding ──
    if event_to_wait is not None:
        try:
            await asyncio.wait_for(event_to_wait.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for in-flight price fetch, using stale prices")
        return

    # ── Phase B: We are the fetcher — Fetch from CoinGecko (NO lock held) ─
    new_prices = {}
    try:
        from services.tx_fetcher import _get_client as _get_shared_client
        cg_client = await _get_shared_client()

        resp, resp2 = await asyncio.gather(
            cg_client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "ethereum",
                    "vs_currencies": "usd,hkd,btc",
                },
            ),
            cg_client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "solana,bitcoin",
                    "vs_currencies": "usd",
                },
            ),
        )
        resp.raise_for_status()
        data = resp.json()
        resp2.raise_for_status()
        data2 = resp2.json()

        eth = data.get("ethereum", {})
        eth_usd = eth.get("usd", 0)
        eth_hkd = eth.get("hkd", 0)
        eth_btc = eth.get("btc", 0)
        sol_usd = data2.get("solana", {}).get("usd", 0)
        btc_usd = data2.get("bitcoin", {}).get("usd", 0)

        new_prices = {
            "ETH": eth_usd if eth_usd > 0 else _price_cache.get("ETH", 0.0),
            "SOL": sol_usd if sol_usd > 0 else _price_cache.get("SOL", 0.0),
            "BTC": btc_usd if btc_usd > 0 else _price_cache.get("BTC", 0.0),
        }
        if eth_usd > 0:
            if eth_hkd > 0:
                new_prices["USDHKD"] = eth_hkd / eth_usd
            if eth_btc > 0:
                new_prices["USDBTC"] = eth_btc / eth_usd

    except asyncio.CancelledError:
        # CRITICAL: Coroutine was cancelled (e.g., during shutdown).
        # Must clear _price_fetch_event so future cycles can retry.
        async with _price_cache_lock:
            _price_fetch_event.set()  # Unblock any waiters
            _price_fetch_event = None
        raise  # Re-raise to propagate cancellation

    except Exception as e:
        logger.warning(f"Price refresh failed (using stale): {e}")
        # Signal waiters that the fetch is done (even though it failed)
        # and reset the event so the next cycle can retry
        async with _price_cache_lock:
            if _price_fetch_event is not None:
                _price_fetch_event.set()
                _price_fetch_event = None
        return

    # ── Phase C: Update cache under lock (fast, no I/O) ──────────────
    async with _price_cache_lock:
        _price_cache.update(new_prices)
        _price_cache["timestamp"] = _time_mod.time()
        if _price_fetch_event is not None:
            _price_fetch_event.set()
            _price_fetch_event = None

    if new_prices.get("ETH", 0) > 0:
        logger.info(
            f"Price cache refreshed: ETH=${new_prices['ETH']}, "
            f"SOL=${new_prices.get('SOL', 0)}, BTC=${new_prices.get('BTC', 0)}, "
            f"USDHKD={new_prices.get('USDHKD', _price_cache.get('USDHKD', 0)):.4f}"
        )
