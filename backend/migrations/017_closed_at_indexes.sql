-- ChainWatch Migration 017
-- Add partial index on closed_at for efficient "recently closed" queries
--
-- This supports fetching recently closed (executed/failed) signals for
-- the signal history view, and efficient pruning of old closed signals.

-- Partial index: only closed signals (most queries filter on closed_at IS NOT NULL)
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_closed_at
    ON copy_trade_signals(closed_at DESC)
    WHERE closed_at IS NOT NULL;

-- Composite index for per-wallet closed signal lookups (used by /api/signals/history)
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_closed
    ON copy_trade_signals(wallet_id, closed_at DESC)
    WHERE closed_at IS NOT NULL;
