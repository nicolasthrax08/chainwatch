# ChainWatch Cycle 2026-06-07 10:00 UTC — Work Product

## Tasks Completed

### Task 1: `write_tool` — Add `/api/health/diagnostic` endpoint

**File modified:** `backend/main.py` (lines 2079-2219, +139 lines)

**What it does:**
A new `GET /api/health/diagnostic` endpoint that provides deep startup diagnostics:

1. **DATABASE_URL parse & DNS resolution** — parses the connection string, extracts host/port/db, attempts `getaddrinfo()` to resolve the hostname to IP. This directly diagnoses the "DB unreachable" issue from OBS-2 of the previous audit.
2. **DB connectivity test** — attempts `SELECT 1` through the pool, measures latency, captures error messages.
3. **TCP reachability test** — independent of asyncpg, attempts a raw TCP socket connection to the DB host:port with 5s timeout. Distinguishes "DNS failure" from "TCP blocked" from "auth failure".
4. **Environment variable presence** — reports which required env vars are set (DATABASE_URL, CRON_SECRET, CHAINWATCH_MASTER_KEY, API keys) without leaking values.
5. **Monitor state** — alive/dead, poll interval, max consecutive errors, price cache age/freshness, current cached prices.
6. **Migration log summary** — count of applied migrations, latest filename and timestamp.
7. **System info** — hostname, Python version, CWD.

**Why this matters:** The previous audit identified that the DB connection failure cascades into monitor-not-starting, price-cache-stale, and task-queue-unavailable. When the next cycle runs and the DB is still down, this endpoint will tell us *exactly* where the failure is (DNS? TCP? auth?).

**Safety:** No secrets leaked. API keys reported as boolean presence only. No write operations.

### Task 2: `review_thresholds` — Signal threshold analysis

**Current thresholds in `signal_generator.py`:**
```python
MIN_SIGNAL_USD_BY_CHAIN = {
    "btc": 10000.0,   # BTC whales: $10K minimum
    "eth": 5000.0,    # ETH whales: $5K minimum
    "sol": 2000.0,    # SOL whales: $2K minimum
}
MIN_SIGNAL_USD_DEFAULT = 5000.0  # fallback
```

**Current monitor config in `monitor.py`:**
```python
POLL_INTERVAL = 60            # seconds
MAX_CONSECUTIVE_ERRORS = 5    # skip after 5 consecutive errors
WALLET_FETCH_TIMEOUT = 25     # seconds per wallet
MAX_CONCURRENT_WALLETS = 5    # semaphore cap
```

**Analysis:**

| Parameter | Current | Assessment | Recommendation |
|-----------|---------|------------|----------------|
| `MIN_SIGNAL_USD_BTC` | $10,000 | Reasonable. BTC whales typically move $10K+ for meaningful trades. Dust txns below this are common. | **Keep** — no change needed. |
| `MIN_SIGNAL_USD_ETH` | $5,000 | Reasonable. ETH gas costs mean sub-$5K txns are often internal transfers or dust. | **Keep** — no change needed. |
| `MIN_SIGNAL_USD_SOL` | $2,000 | Reasonable. SOL txns are cheaper, so the floor is lower. $2K filters dust while capturing meaningful SOL whale activity. | **Keep** — no change needed. |
| `POLL_INTERVAL` | 60s | Good balance between freshness and API rate limits. At 60s with 5 concurrent wallets, a 20-wallet portfolio takes ~4 minutes to fully poll. | **Keep** — no change needed. |
| `MAX_CONSECUTIVE_ERRORS` | 5 | From previous audit (MINOR-1 in 0400 cycle): "may be too aggressive for unreliable RPC endpoints." 5 consecutive errors = ~5 minutes of failure before skip. | **Consider raising to 8** — gives ~8 minutes of tolerance for transient RPC outages while still preventing permanent broken wallets from consuming semaphore slots. |
| `WALLET_FETCH_TIMEOUT` | 25s | Reasonable for RPC calls. Etherscan/Solscan can be slow under load. | **Keep** — no change needed. |
| `MAX_CONCURRENT_WALLETS` | 5 | Good for API rate limit management. | **Keep** — no change needed. |
| `DEDUP_INTERVAL` | 5 minutes | Prevents signal spam. Matches the in-memory dedup TTL (300s). | **Keep** — no change needed. |
| `_COOLDOWN_SECONDS` (alerts) | 300s (5 min) | Prevents alert spam. Prunes at 2x TTL (600s). | **Keep** — no change needed. |

**Threshold verdict:** All signal thresholds are well-calibrated. The only parameter worth tuning is `MAX_CONSECUTIVE_ERRORS` (5 → 8), which is a minor resilience improvement, not urgent.

**Decision:** DEFER the `MAX_CONSECUTIVE_ERRORS` change. The current value is acceptable and the change is low-impact. Will revisit when more wallets are tracked and RPC reliability data is available.

---

## Audit Results

- **90/90 checks passed** (up from 88 — new patterns detected in the diagnostic endpoint)
- **0 critical findings**
- **0 minor findings**

## Files Modified

1. `backend/main.py` — Added `/api/health/diagnostic` endpoint (+139 lines)

## Git Status

- All changes committed
- Ready for Zeabur deployment

## Follow-up Tasks Generated

1. `audit_outputs` — Verify the diagnostic endpoint returns correct data when DB is unreachable vs. reachable
2. `analyze_failures` — Use the diagnostic endpoint output to pinpoint the DB connection failure mode
3. `review_thresholds` — Revisit MAX_CONSECUTIVE_ERRORS after gathering RPC reliability data
