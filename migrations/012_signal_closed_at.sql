-- ChainWatch Migration 012
-- Add closed_at column to copy_trade_signals table
--
-- Required by:
--   monitor.py:663  — stale expiry: SET closed_at = NOW()
--   main.py:2244    — mirror_trade fail: SET closed_at = NOW()
--   main.py:2260    — mirror_trade fail: SET closed_at = NOW()
--   main.py:2275    — mirror_trade fail: SET closed_at = NOW()
--   main.py:2289    — mirror_trade exec: SET closed_at = NOW()
--   main.py:1905-1934 — /api/signals/history filters on closed_at
--
-- Without this column, the stale expiry and signal history endpoints
-- raise UndefinedColumnError at runtime.

ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;

-- Index for the history endpoint which filters on closed_at IS NOT NULL
-- and orders by closed_at DESC
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_closed_at
    ON copy_trade_signals(closed_at DESC)
    WHERE closed_at IS NOT NULL;
