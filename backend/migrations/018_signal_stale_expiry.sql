-- Migration 018: Signal stale expiry support
-- Adds partial index for efficient stale-signal queries and documents
-- the 'stale' status as a terminal state alongside 'executed' and 'failed'.
--
-- The 'stale' status is applied to signals that have been 'pending' for
-- longer than SIGNAL_STALE_THRESHOLD_HOURS (default 72h). This prevents
-- signals from living forever when mirror_trade is never invoked
-- (e.g., user has no Alpaca keys, or the signal was deprioritised).

-- Partial index for fast stale-signal lookups
-- Covers the monitor's WHERE status = 'pending' AND created_at < NOW() - INTERVAL check
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_copy_trade_signals_pending_created'
    ) THEN
        CREATE INDEX idx_copy_trade_signals_pending_created
            ON copy_trade_signals (created_at)
            WHERE status = 'pending';
    END IF;
END
$$;

-- Extend the CHECK constraint to include 'stale' status if it exists
DO $$
BEGIN
    -- Drop existing constraint if present (to add 'stale')
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_copy_trade_signals_status'
          AND conrelid = 'copy_trade_signals'::regclass
    ) THEN
        ALTER TABLE copy_trade_signals
            DROP CONSTRAINT chk_copy_trade_signals_status;
    END IF;
    -- Re-create with 'stale' included
    ALTER TABLE copy_trade_signals
        ADD CONSTRAINT chk_copy_trade_signals_status
            CHECK (status IN ('pending', 'executed', 'failed', 'stale'));
END
$$;

COMMENT ON INDEX idx_copy_trade_signals_pending_created IS
    'Supports fast lookup of stale pending signals for auto-expiry (monitor Phase 7).';
