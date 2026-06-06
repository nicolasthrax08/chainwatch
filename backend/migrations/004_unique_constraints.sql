-- Migration: Add unique constraint on transactions.tx_hash to make ON CONFLICT DO NOTHING work
-- Related pitfall: #19 — ON CONFLICT DO NOTHING requires a matching unique index
-- Without this constraint, the monitor silently inserts duplicate transactions every poll cycle.

-- Add unique constraint for tx_hash + chain (same tx_hash can exist on different chains)
CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_tx_hash_chain
    ON transactions (tx_hash, chain);

-- Add unique constraint on users.wallet_address (used by ON CONFLICT in auth/verify)
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_wallet_address
    ON users (wallet_address);
