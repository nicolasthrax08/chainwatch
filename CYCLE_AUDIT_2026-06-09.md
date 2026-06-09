# ChainWatch Code Audit — Cycle 2026-06-09

## Health Summary (from live API)
- **Public health**: 503 degraded — `db.ok: false`, `monitor.alive: false`, price cache stale
- **Internal health**: 200 ok — `database: "in_memory"` (no PostgreSQL configured)
- **Frontend**: ✅ serving correctly
- **Auth**: ✅ working (challenge + verify)
- **API keys**: Etherscan ✅, Solscan ✅, Alpaca ✅, Blockchair ❌, Telegram ❌
- **Task queue API**: ❌ 404 (not deployed — older version running)

## Critical Findings

### C1: Monitor Dead — No PostgreSQL = No Monitor
The monitor worker (`start_monitor`) requires a non-None `db_pool`. Since DATABASE_URL points to an unreachable host, `db_pool` is None, and `start_monitor` returns immediately. This means:
- No wallet balance polling
- No transaction detection
- No signal generation
- No alert evaluation
- Price cache never refreshes (stuck at hardcoded defaults)

**Fix needed**: Configure DATABASE_URL in Zeabur dashboard to point to the PostgreSQL service.

### C2: Task Queue API Not Deployed
The deployed version (internal) returns 404 for `/api/task-queue/*` endpoints and `/api/health/diagnostic`. The codebase has these endpoints but they're not live. The Hermes cron loop cannot function without this API.

**Fix needed**: Redeploy the latest code from the repo.

### C3: CRON_SECRET Not Configured
Even after deployment, the task queue endpoints require `CRON_SECRET` env var. Without it, all task queue calls return 503 "Task queue auth not configured on server". The cron job needs this to interact with the queue.

**Fix needed**: Generate and set CRON_SECRET in Zeabur environment.

## Minor Findings

### M1: CoinGecko Rate Limit Resilience
The `_ensure_prices_fetched()` function in monitor.py holds `_price_cache_lock` through the entire HTTP fetch (two concurrent CoinGecko requests). If CoinGecko is rate-limited (429), the lock is held for the full timeout duration, blocking all wallet processing. Consider releasing the lock before the HTTP call and re-acquiring for the update.

### N+1 Query in Signal Generation (Phase 6b)
The signal generation loop in `_poll_all_wallets_inner()` opens a new DB connection per wallet (`async with _pool.acquire() as conn` inside the for loop). For 10 changed wallets, this means 10 separate connection acquisitions. While not holding the connection across I/O (good), it's still inefficient. A single batch query for wallet labels would be better.

### `acquire_db()` Returns Context Manager, Not Connection
The `acquire_db()` function returns `db_pool.acquire()` which is an async context manager. Callers use `async with acquire_db() as conn`. This is correct but the function name is slightly misleading — it acquires a connection from the pool, not the pool itself.

### JWT `cat` Claim for created_at
The `create_jwt` function embeds `created_at` as `"cat"` claim. Non-standard abbreviation — consider `"created_at"` for clarity, or document the convention.

### Whale Sentiment Filter
The `get_whale_sentiment` endpoint filters by `w.user_id = $1 AND w.is_whale = TRUE`. This means a user who hasn't flagged any wallet as whale gets neutral sentiment. Consider also including wallets that the system auto-classifies as whale (based on balance thresholds).

## Architecture Verdict
The codebase is well-structured with good separation of concerns. The monitor-worker pattern with phase isolation (Phase 1-6) is solid. The signal generator and whale scorer are well-designed. The main blocker is infrastructure (DB not configured, code not deployed), not code quality.

## Recommended Actions (Priority Order)
1. **Configure DATABASE_URL** in Zeabur dashboard → ChainWatch service → Environment Variables
2. **Deploy latest code** (includes task queue API, diagnostics, health endpoints)
3. **Set CRON_SECRET** env var for task queue auth
4. **Run migrations** (011_task_queue.sql and any pending)
5. **Verify monitor starts** via /api/health/diagnostic
6. **Set BLOCKCHAIR_API_KEY** and **TELEGRAM_BOT_TOKEN** for full functionality
