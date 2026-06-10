-- Migration 019: Add unique constraint on wallets(user_id, address)
-- Required for ON CONFLICT in add_wallet endpoint (main.py).
-- Without this, duplicate address inserts raise unhandled UniqueViolationError (500).
--
-- Also adds defense-in-depth: the add_wallet endpoint uses ON CONFLICT DO UPDATE
-- to gracefully handle re-adds of the same address by the same user.

-- First, clean up any existing duplicates (keep the earliest per user_id+address)
DELETE FROM wallets a USING wallets b
WHERE a.id > b.id
  AND a.user_id = b.user_id
  AND a.address = b.address;

-- Add composite unique constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_wallets_user_address'
    ) THEN
        ALTER TABLE wallets ADD CONSTRAINT uq_wallets_user_address
            UNIQUE (user_id, address);
    END IF;
END$$;

COMMENT ON CONSTRAINT uq_wallets_user_address ON wallets IS
    'Prevents duplicate wallet addresses per user; required for ON CONFLICT in add_wallet.';
