-- ChainWatch Migration 014
-- Performance: Pre-compute global_median_30d once per monitor cycle.
--
-- This migration does NOT add any schema changes — it documents the
-- code-level optimization in monitor.py + whale_scorer.py that eliminates
-- the per-wallet global_median subquery (O(N) → O(1) per cycle).
--
-- The optimization:
--   - monitor.py pre-computes global_median_30d once before Phase 6
--   - whale_scorer.score_whale_wallet() accepts optional global_median_30d param
--   - When param > 0, the per-wallet subquery is skipped entirely
--   - Fallback: when param == 0 (called outside monitor), original subquery runs
--
-- No schema changes needed — this is a code-only optimization.
-- This migration serves as documentation and a migration log marker.

-- Verify the required indexes exist (idempotent)
-- idx_copy_trade_signals_wallet_created: (wallet_id, created_at) — from migration 013
-- idx_copy_trade_signals_wallet_status_created: (wallet_id, status, created_at) — from migration 013
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_copy_trade_signals_wallet_created'
    ) THEN
        CREATE INDEX idx_copy_trade_signals_wallet_created
            ON copy_trade_signals(wallet_id, created_at);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_copy_trade_signals_wallet_status_created'
    ) THEN
        CREATE INDEX idx_copy_trade_signals_wallet_status_created
            ON copy_trade_signals(wallet_id, status, created_at);
    END IF;
END$$;
