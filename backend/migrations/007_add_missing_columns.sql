-- ChainWatch Migration 007
-- Adds missing columns and constraints required by the monitor worker and signal generator.
-- These columns/constraints are referenced in application code but were never added to the schema.

-- 1. Add balance_native column to wallets table (used by monitor.py Phase 5 UPDATE)
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_native DECIMAL(30, 18) DEFAULT 0;

-- 2. Add balance_usd column to wallets table (used by monitor.py Phase 5 UPDATE, dashboard reads it)
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_usd DECIMAL(20, 2) DEFAULT 0;

-- 3. Add last_balance_update column to wallets table (used by monitor.py Phase 5 UPDATE)
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_balance_update TIMESTAMP WITH TIME ZONE;

-- 4. Add unique constraint on transactions(tx_hash, chain) so ON CONFLICT DO NOTHING works
-- Without this, the ON CONFLICT clause never triggers and duplicates are silently inserted.
-- CRIT-2 FIX: Use IF NOT EXISTS guard to avoid "constraint already exists" error if
-- migration 002 has already created this constraint. Idempotent migration.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_transactions_tx_hash_chain'
    ) THEN
        ALTER TABLE transactions ADD CONSTRAINT uq_transactions_tx_hash_chain
            UNIQUE (tx_hash, chain);
    END IF;
END$$;

-- 5. Add whale_score columns to wallets table (used by monitor.py Phase 6b score write-back)
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS whale_score DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_activity DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_reliability DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_weight DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_recency DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_diversity DECIMAL(5, 3) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_signals_used INT DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_calculated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_is_coldstart BOOLEAN DEFAULT TRUE;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS median_amount_30d DECIMAL(20, 2) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS execution_rate_30d DECIMAL(5, 3) DEFAULT 0;

-- 6. Add explanation column to copy_trade_signals (used by signal_generator.py)
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS explanation TEXT;
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS explanation_stale BOOLEAN DEFAULT FALSE;
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS score_at_generation DECIMAL(5, 3) DEFAULT 0;
