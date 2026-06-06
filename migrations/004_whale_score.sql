-- Migration 004: Whale scoring model + signal explanation columns

-- ── wallets table: scoring columns ─────────────────────────────────────
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS whale_score DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_activity DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_reliability DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_weight DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_recency DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_diversity DECIMAL(4,3) DEFAULT 0.000;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_signals_used INT DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_calculated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS score_is_coldstart BOOLEAN DEFAULT TRUE;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS median_amount_30d DECIMAL(18,2) DEFAULT 0;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS execution_rate_30d DECIMAL(4,3) DEFAULT 0.000;

-- ── copy_trade_signals table: explanation columns ──────────────────────
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS explanation TEXT;
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS explanation_stale BOOLEAN DEFAULT FALSE;
ALTER TABLE copy_trade_signals ADD COLUMN IF NOT EXISTS score_at_generation DECIMAL(4,3);
