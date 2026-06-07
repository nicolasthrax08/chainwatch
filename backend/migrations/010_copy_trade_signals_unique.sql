-- ChainWatch Migration 010
-- Fix: Add unique constraint on copy_trade_signals so ON CONFLICT DO NOTHING works.
-- The INSERT in monitor.py uses ON CONFLICT DO NOTHING but no unique
-- constraint exists on the table, so duplicates are silently inserted every poll cycle.
-- Natural dedup key: (wallet_id, token_symbol, action, amount_usd) — same signal
-- shouldn't be recorded twice with identical values.

-- First, clean up any existing duplicates (keep the earliest)
DELETE FROM copy_trade_signals a USING copy_trade_signals b
WHERE a.id > b.id
  AND a.wallet_id = b.wallet_id
  AND a.token_symbol = b.token_symbol
  AND a.action = b.action
  AND a.amount_usd = b.amount_usd;

-- Add composite unique constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_copy_trade_signals_dedup'
    ) THEN
        ALTER TABLE copy_trade_signals ADD CONSTRAINT uq_copy_trade_signals_dedup
            UNIQUE (wallet_id, token_symbol, action, amount_usd);
    END IF;
END$$;

-- Add index for efficient signal lookups by wallet
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_action
    ON copy_trade_signals(wallet_id, action);
