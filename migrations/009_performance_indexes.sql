-- ChainWatch Migration 009
-- Performance: Add composite indexes for whale_scorer query pattern.
--
-- The whale_scorer.score_whale_wallet() query filters copy_trade_signals
-- by (wallet_id, created_at) with multiple WHERE clauses like:
--   WHERE cts.created_at >= NOW() - INTERVAL '30 days'
--   WHERE cts.created_at >= NOW() - INTERVAL '90 days'
--   WHERE cts.status = 'executed' AND cts.created_at >= NOW() - INTERVAL '30 days'
--
-- Existing indexes:
--   idx_copy_trade_signals_wallet_id (wallet_id) — single column, no created_at
--   idx_copy_trade_signals_wallet_action (wallet_id, action) — wrong second column
--
-- The composite index on (wallet_id, created_at) allows index-only scans
-- for all these filter patterns, reducing the per-wallet scoring query
-- from a full table scan to a narrow index range scan.
-- This is critical when the monitor tracks many whale wallets, as the
-- scoring query runs once per whale per poll cycle.

-- Composite index for the main scoring query filter pattern
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_created
    ON copy_trade_signals(wallet_id, created_at);

-- Composite index for execution rate calculation (wallet_id + status + created_at)
-- Covers: WHERE cts.status = 'executed' AND cts.created_at >= NOW() - INTERVAL '30 days'
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_status_created
    ON copy_trade_signals(wallet_id, status, created_at);
