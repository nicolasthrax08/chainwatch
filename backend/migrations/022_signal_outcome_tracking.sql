-- ChainWatch Migration 022
-- Add user-reported outcome tracking to copy_trade_signals
--
-- Currently signals track system status (pending/executed/failed/stale) but have
-- no way for users to record their actual P&L when they follow a signal.
-- This migration adds:
--   - user_pnl_usd:   P&L in USD the user realized by following the signal
--   - user_outcome:   Enum: 'profit' | 'loss' | 'breakeven' | 'skipped' | NULL
--   - user_notes:     Free-text field for user to add context
--   - reviewed_at:    Timestamp when the user reviewed the signal
--
-- These columns are all nullable — most signals will never be manually reviewed.

-- Add user_pnl_usd column
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS user_pnl_usd DECIMAL(20, 2);

-- Add user_outcome column (enum as CHECK constraint for portability)
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS user_outcome VARCHAR(20)
    CHECK (user_outcome IS NULL OR user_outcome IN ('profit', 'loss', 'breakeven', 'skipped'));

-- Add user_notes column (free-text, max 500 chars)
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS user_notes VARCHAR(500);

-- Add reviewed_at timestamp
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE;

-- Index for outcome stats aggregation queries
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_outcome
    ON copy_trade_signals(user_outcome, user_pnl_usd)
    WHERE user_outcome IS NOT NULL;

-- Index for reviewed_at to support "recently reviewed" queries
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_reviewed_at
    ON copy_trade_signals(reviewed_at DESC)
    WHERE reviewed_at IS NOT NULL;

-- Note: No unique constraint needed — one review per signal is enforced by
-- the POST endpoint (updates the existing row, not INSERT).
