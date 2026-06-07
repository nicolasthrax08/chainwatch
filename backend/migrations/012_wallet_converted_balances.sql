-- ChainWatch Migration 012
-- Adds balance_hkd and balance_btc columns to wallets table.
-- These are needed by the list_wallets endpoint and the refresh_wallet endpoint
-- to persist converted balance values so the frontend can display them
-- without requiring on-the-fly conversion.

-- 1. Add balance_hkd column to wallets table
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_hkd DECIMAL(20, 2) DEFAULT 0;

-- 2. Add balance_btc column to wallets table
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_btc DECIMAL(20, 8) DEFAULT 0;

-- 3. Backfill existing wallets: compute from balance_usd using approximate rates
--    This ensures existing wallets show correct converted balances immediately.
--    Rates: USDHKD ≈ 7.8, USDBTC ≈ 1/62000
UPDATE wallets
SET balance_hkd = COALESCE(balance_usd, 0) * 7.8,
    balance_btc = COALESCE(balance_usd, 0) / 62000.0
WHERE balance_hkd = 0 AND balance_btc = 0 AND balance_usd IS NOT NULL;
