# Cycle 2026-06-12 08:00 — Deep Code Audit & Threshold Review

## Task
Self-generated: `review_thresholds` + `audit_outputs` (all pending tasks blocked on DB)

## Scope
Full pitfall checklist walkthrough of ChainWatch codebase (backend + frontend).
Focus on: threshold review, code quality gaps, and any issues missed by previous solo audits.

---

## Part 1: Threshold Review

### MIN_WHALE_SCORE (0.30)
- **Current**: 0.30 (raised from 0.20 in cycle 2026-06-12)
- **Assessment**: REASONABLE. At 0.30, C_final is capped at 0.65 for borderline whales (0.5 * 0.65 + 0.5 * 0.30 = 0.475 for a whale_score=0.30 wallet with max confidence). This filters noise from balance-only whales while capturing mid-tier ones.
- **No change needed**.

### MIN_SIGNAL_USD_BY_CHAIN
- **Current**: btc=$10K, eth=$5K, sol=$2K, default=$5K
- **Assessment**: REASONABLE. BTC $10K ≈ 0.1 BTC (dust threshold), ETH $5K ≈ 2 ETH, SOL $2K ≈ 12 SOL. These filter out spam dust while capturing meaningful whale moves.
- **No change needed**.

### SIGNAL_STALE_THRESHOLD_HOURS (72h)
- **Current**: 72 hours (3 days)
- **Assessment**: REASONABLE. Signals that sit unmirrored for 3 days are unlikely to be acted upon. 72h balances between false expiry (too short) and stale clutter (too long).
- **No change needed**.

### MAX_CONSECUTIVE_ERRORS (5)
- **Current**: 5
- **Assessment**: REASONABLE. After 5 consecutive failures, the wallet is retried on the next cycle (counter resets). This avoids permanent skipping while backing off persistently failing RPCs.
- **No change needed**.

### Alert Cooldown (300s)
- **Current**: 5 minutes per alert
- **Assessment**: REASONABLE. Prevents alert spam during volatile periods.
- **No change needed**.

### Mirror Rate Limit (10 req/min, burst 5)
- **Current**: Token bucket, per-user
- **Assessment**: REASONABLE. Prevents abuse while allowing legitimate rapid mirroring.
- **No change needed**.

---

## Part 2: Pitfall Checklist Walkthrough

### Pitfall #6: try/except import scoping
- **Status**: CLEAN. All imports are at module level. No try-block imports found.
- **Evidence**: Verified all `from services.X import Y` statements are at module level in all 16 service files.

### Pitfall #7: DB connection held across HTTP calls
- **Status**: CLEAN. `_ensure_prices_fetched()` explicitly releases lock before HTTP I/O. Telegram sends are fire-and-forget outside DB scope.
- **Evidence**: monitor.py Phase B (line 846-907) has no lock held during CoinGecko HTTP calls.

### Pitfall #8: Write-then-re-read race
- **Status**: CLEAN. Signal generation uses in-memory data after INSERT, no re-read of the same row.

### Pitfall #12: Unbounded state dicts
- **Status**: CLEAN. All state dicts have pruning:
  - `_signal_dedup_cache`: TTL-based pruning every check
  - `_mirror_rate_buckets`: TTL-based pruning
  - `_cooldown_cache`: Prune on read + time-based expiry
  - `_cycle_stats`: Ring buffer (max 20)
  - `_last_balances`, `_last_tx_hashes`, `_consecutive_errors`: Pruned when wallets are removed

### Pitfall #15: Dead variables
- **Status**: CLEAN. No dead variables found after recent refactoring.

### Pitfall #19: ON CONFLICT requires unique constraint
- **Status**: NEEDS VERIFICATION (requires DB). The `copy_trade_signals` INSERT at signal_generator.py:274 uses `ON CONFLICT (wallet_id, token_symbol, action, amount_usd) DO NOTHING`. This requires a unique index on those columns.
- **Risk**: MEDIUM. If the unique index doesn't exist, duplicates are silently inserted.
- **Action**: Verify in DB migration. (Blocked by DB access.)

### Pitfall #20: Code references DB columns not in schema
- **Status**: NEEDS VERIFICATION (requires DB). The whale_scorer.py query references `w.balance_usd`, `w.created_at`, `w.is_whale`, `cts.wallet_id`, `cts.amount_usd`, `cts.created_at`, `cts.status`. These all appear to be in the schema based on previous migrations, but cannot verify without DB.
- **Risk**: LOW. These columns have been in the schema since early migrations.

### Pitfall #23: Phase isolation in monitor workers
- **Status**: CLEAN. Phase 6 (signal gen + alerts) uses separate `async with _pool.acquire() as conn` blocks. Phase 7 (stale expiry) also uses a separate connection.

### Pitfall #24: Balance-vs-event-amount conflation
- **Status**: CLEAN. signal_generator.py correctly uses `tx_amount_native` (event-level) for signal amount, not `balance_native` (aggregate). The `amount_usd` in signals is computed from `tx_amount_native * price_usd`.

### Pitfall #25: WS auth-before-accept
- **Status**: CLEAN. websocket_manager.py `connect()` accepts inside the lock after the caller has authenticated (auth is done in main.py before calling ws_manager.connect).

### Pitfall #27: Patch tool consuming adjacent definitions
- **Status**: N/A (no patches being applied this cycle).

### Pitfall #31: $N parameter renumbering
- **Status**: CLEAN. All queries use either `$1, $2, ...` sequential or `ANY($1)` array params. No cases of adding filters to existing parameterized queries.

---

## Part 3: Code Quality Findings

### Finding C1 (CRITICAL): `_signal_dedup_cache` is global mutable state without process-level isolation
- **File**: `backend/services/signal_generator.py:35-36`
- **Issue**: `_signal_dedup_cache` is a module-level dict. If ChainWatch runs with multiple workers (e.g., gunicorn with multiple workers), each process has its own dedup cache. A signal could be duplicated across workers.
- **Severity**: LOW in current deployment (single Zeabur container = single process). MEDIUM if multi-worker deployment is added.
- **Recommendation**: Document this limitation. If multi-worker is needed, replace with Redis or DB-based dedup.

### Finding M1 (MINOR): `fetch_eth_transactions` returns empty without API key
- **File**: `backend/services/tx_fetcher.py:196-198`
- **Issue**: If `ETHERSCAN_API_KEY` is not set, ETH transaction fetching silently returns `[]`. This means ETH whales will never generate signals (only balance changes are tracked).
- **Severity**: MEDIUM. The health check shows `etherscan: true` (key is set), so this is not currently broken. But if the key expires, ETH signals silently stop.
- **Recommendation**: Add a warning log at startup if the key is missing.

### Finding M2 (MINOR): `fetch_sol_transactions` can fail on rate-limited Solana RPC
- **File**: `backend/services/tx_fetcher.py:262-384`
- **Issue**: The Solana public RPC (`api.mainnet-beta.solana.com`) is heavily rate-limited. During high wallet counts, the concurrent `getTransaction` calls can hit 429s. The retry logic handles this, but with `MAX_RETRIES=2` and up to 10 concurrent calls per wallet, the effective retry budget is 30 HTTP calls per wallet per cycle.
- **Severity**: LOW. Retry with backoff handles this. The semaphore (5 concurrent) limits the blast radius.
- **Recommendation**: Consider reducing `limit` from 10 to 3 for SOL to reduce RPC pressure.

### Finding M3 (MINOR): `whale_scorer.py` global_median subquery fallback is O(N)
- **File**: `backend/services/whale_scorer.py:55-64`
- **Issue**: When `global_median_30d=0` (default), the per-wallet subquery runs an O(N) `PERCENTILE_CONT` over all whale signals. The monitor pre-computes this (line 396-415), so in practice the fallback is only used for ad-hoc scoring.
- **Severity**: LOW. The monitor always passes the pre-computed value.
- **Recommendation**: Document that the fallback is for ad-hoc use only.

### Finding M4 (MINOR): `signal_generator.py` explanation templates have hardcoded size bucket logic
- **File**: `backend/services/signal_generator.py:111-120`
- **Issue**: The `size_bucket` logic (`ratio >= 2.0` → large, `>= 0.5` → med, else small) is only applied to receive signals (TPL-B) and buy+proven signals (TPL-D/E/F). Buy+new signals (TPL-H/I/J/K) don't use size_bucket at all.
- **Severity**: LOW. This is a design choice (new whales don't have enough history for size comparison). The fallback TPL-Z template handles any gaps.
- **Recommendation**: Consider adding size context for new whales too (e.g., "relative to their balance").

### Finding M5 (MINOR): `monitor.py` `_prev_balance_map` computed outside transaction
- **File**: `backend/services/monitor.py:321-333`
- **Issue**: The prev_balance_map is fetched in a separate `async with _pool.acquire()` block (line 326-333) before the Phase 5 transaction (line 335). Between the fetch and the transaction, another poll cycle could update balances. However, since `_poll_lock` prevents concurrent poll cycles, this is safe.
- **Severity**: LOW. The `_poll_lock` ensures serialization. Documented for clarity.

---

## Part 4: Architecture Assessment

### Overall: SOUND (4.5/5)

**Strengths**:
1. Clean separation of concerns: monitor → whale_scorer → signal_generator → alert_evaluator
2. Proper phase isolation with separate DB connections
3. Comprehensive test coverage (689 tests)
4. Good concurrency patterns: asyncio.Event for price fetch coordination, semaphores for RPC limiting
5. Well-documented pitfall fixes throughout the codebase
6. Field contract validator catches frontend-backend mismatches

**Weaknesses**:
1. DB connectivity has been down for 5+ cycles — the single biggest operational risk
2. No multi-worker dedup isolation (currently fine for single container)
3. Solana RPC rate limiting could become an issue with >20 tracked wallets

---

## Part 5: Recommended Actions

1. **DB Connectivity** (HIGH): Investigate Zeabur DATABASE_URL configuration. This is the primary blocker.
2. **Verify ON CONFLICT constraint** (MEDIUM): Ensure `copy_trade_signals` has a unique index on `(wallet_id, token_symbol, action, amount_usd)`.
3. **Add startup warning for missing Etherscan key** (LOW): Log a warning if `ETHERSCAN_API_KEY` is not set.
4. **Document single-worker dedup limitation** (LOW): Add a comment in signal_generator.py noting the process-local dedup cache limitation.
