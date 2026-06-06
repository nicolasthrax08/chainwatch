-- ChainWatch Migration 005
-- Consolidates all missing columns/constraints from backend/migrations/ into root migrations/
-- This brings the root migration set to parity with backend/migrations/ so that
-- running only root migrations/* produces the same schema as running backend/migrations/*.
--
-- Missing from root:
--   - alerts.last_fired_at (from backend 005)
--   - users.alpaca_* columns (from backend 006)
--   - uq_users_wallet_address unique index (from backend 004)
--   - idx_users_session_token partial index (from backend 005)
--   - idx_alerts_last_fired_at partial index (from backend 005)
--   - uq_fired_alerts_alert_trigger unique constraint (from backend 008)
--   - idx_fired_alerts_alert_id_trigger index (from backend 008)

-- ── alerts.last_fired_at ─────────────────────────────────────────────
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_fired_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_alerts_last_fired_at
    ON alerts(last_fired_at) WHERE last_fired_at IS NOT NULL;

-- ── users.alpaca columns ─────────────────────────────────────────────
-- Per-user Alpaca paper trading API keys (nullable; NULL = not connected)
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_api_key_enc TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_api_key_iv TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_secret_key_enc TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_secret_key_iv TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_paper_account_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_connected_at TIMESTAMP WITH TIME ZONE;

-- ── Unique constraint on users.wallet_address ────────────────────────
-- Used by ON CONFLICT (wallet_address) DO UPDATE in auth/verify endpoint.
-- The UNIQUE constraint already exists on the column definition in 001, but
-- this explicit index makes it available for ON CONFLICT targeting.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_wallet_address
    ON users(wallet_address);

-- ── Partial index for session token lookups ──────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_session_token
    ON users(session_token) WHERE session_token IS NOT NULL;

-- ── Unique constraint on fired_alerts(alert_id, trigger_value) ───────
-- Required for ON CONFLICT DO NOTHING in alert_evaluator.py line 231.
-- Without this, the same alert can be recorded multiple times per cycle.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fired_alerts_alert_trigger'
    ) THEN
        ALTER TABLE fired_alerts ADD CONSTRAINT uq_fired_alerts_alert_trigger
            UNIQUE (alert_id, trigger_value);
    END IF;
END$$;

-- ── Index for efficient alert cooldown lookups ───────────────────────
CREATE INDEX IF NOT EXISTS idx_fired_alerts_alert_id_trigger
    ON fired_alerts(alert_id, trigger_value);
