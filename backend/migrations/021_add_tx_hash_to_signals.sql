-- ChainWatch Migration 021
-- Add tx_hash column to copy_trade_signals for on-chain tx audit trail.
-- Currently signals have no link to the specific on-chain transaction that
-- triggered them. This makes it impossible to verify signal provenance
-- or deduplicate at the tx level (wallet+token+amount dedup is coarse).

-- Add tx_hash column (nullable for backward compatibility with existing rows)
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS tx_hash TEXT;

-- Add index for tx_hash lookups (used by dedup check and API endpoint)
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_tx_hash
    ON copy_trade_signals(tx_hash)
    WHERE tx_hash IS NOT NULL;

-- Add unique constraint on (wallet_id, tx_hash) to prevent duplicate signals
-- for the same on-chain transaction. This is a tighter dedup than the
-- existing (wallet_id, token_symbol, action, amount_usd) constraint because
-- it catches the exact same tx even if amount rounding differs.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_copy_trade_signals_wallet_tx'
    ) THEN
        ALTER TABLE copy_trade_signals ADD CONSTRAINT uq_copy_trade_signals_wallet_tx
            UNIQUE (wallet_id, tx_hash);
    END IF;
END$$;
