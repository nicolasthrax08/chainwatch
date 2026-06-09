-- ChainWatch Migration 016
-- Add signal performance tracking: executed_at tracking + performance indexes
--
-- This migration supports the new /api/signals/stats endpoint that aggregates
-- signal performance (execution rate, avg confidence by status, etc.)

-- Add executed_at column if not already present (should exist from 001, but guard)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'copy_trade_signals' AND column_name = 'executed_at'
    ) THEN
        ALTER TABLE copy_trade_signals ADD COLUMN executed_at TIMESTAMP WITH TIME ZONE;
    END IF;
END$$;

-- Add closed_at column for signals that are no longer active (failed/expired)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'copy_trade_signals' AND column_name = 'closed_at'
    ) THEN
        ALTER TABLE copy_trade_signals ADD COLUMN closed_at TIMESTAMP WITH TIME ZONE;
    END IF;
END$$;

-- Performance index for signal stats aggregation queries
-- Covers the most common stats query pattern: filter by wallet_id + status + date range
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_status_created
    ON copy_trade_signals(status, created_at DESC);

-- Index for executed_at to support time-to-execution analytics
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_executed_at
    ON copy_trade_signals(executed_at DESC)
    WHERE executed_at IS NOT NULL;

-- Composite index for the stats endpoint (user's wallets, grouped by status)
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_status
    ON copy_trade_signals(wallet_id, status, created_at DESC);
