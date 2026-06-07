-- ChainWatch Migration 007
-- Adds missing balance_hkd and balance_btc columns to wallets table
-- Required by refresh_wallet endpoint (main.py:1055-1056) which UPDATEs these columns,
-- and by list_wallets endpoint (main.py:857-858) which reads them.
-- Also required by monitor.py Phase 5 UPDATE to keep all currency columns in sync.
-- Run after 006_fired_alerts_message.sql

ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_hkd DECIMAL(20, 2) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS balance_btc DECIMAL(20, 8) DEFAULT 0;
