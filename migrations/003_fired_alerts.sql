-- ChainWatch Migration 003
-- Adds fired_alerts table for alert history/audit trail
-- Run after 002_fix_missing_columns_and_constraints.sql

CREATE TABLE IF NOT EXISTS fired_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id UUID REFERENCES alerts(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    rule_type VARCHAR(50) NOT NULL,
    trigger_value DECIMAL(20, 2),
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fired_alerts_user_id ON fired_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_fired_alerts_alert_id ON fired_alerts(alert_id);
CREATE INDEX IF NOT EXISTS idx_fired_alerts_created_at ON fired_alerts(created_at DESC);

-- Add unique constraint for dedup: same alert+user+rule within 5-min window
-- Using a partial unique index on (alert_id, user_id, rule_type) with time bucketing
-- For simplicity, we use (alert_id, user_id, rule_type, created_at) and handle
-- dedup in application code (5-minute cooldown check before INSERT)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fired_alerts_dedup'
    ) THEN
        ALTER TABLE fired_alerts
            ADD CONSTRAINT uq_fired_alerts_dedup
            UNIQUE (alert_id, user_id, rule_type, created_at);
    END IF;
END$$;

-- Add cooldown column to alerts table (seconds between repeated fires)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'alerts' AND column_name = 'cooldown_seconds'
    ) THEN
        ALTER TABLE alerts ADD COLUMN cooldown_seconds INTEGER DEFAULT 300;
    END IF;
END$$;
