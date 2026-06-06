-- ChainWatch Migration 002
-- Fixes for missing columns and constraints
-- Run after 001_initial_schema.sql

-- Add missing columns to wallets table (required by monitor.py)
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_native DECIMAL(30, 18) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_usd DECIMAL(20, 2) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_balance_update TIMESTAMP WITH TIME ZONE;

-- Add unique constraint on transactions(tx_hash, chain) for ON CONFLICT DO NOTHING to work
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_transactions_tx_hash_chain'
    ) THEN
        ALTER TABLE transactions ADD CONSTRAINT uq_transactions_tx_hash_chain UNIQUE (tx_hash, chain);
    END IF;
END$$;

-- Add unique constraint on copy_trade_signals to prevent duplicate signals
-- Using (wallet_id, token_symbol, created_at) as the natural unique key
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_copy_trade_signals'
    ) THEN
        -- First dedup any existing duplicates (keep the oldest)
        DELETE FROM copy_trade_signals a USING copy_trade_signals b
        WHERE a.id > b.id
          AND a.wallet_id = b.wallet_id
          AND a.token_symbol = b.token_symbol
          AND a.created_at = b.created_at;

        ALTER TABLE copy_trade_signals
            ADD CONSTRAINT uq_copy_trade_signals
            UNIQUE (wallet_id, token_symbol, created_at);
    END IF;
END$$;
