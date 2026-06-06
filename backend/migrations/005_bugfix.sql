-- ChainWatch Bugfix Migration 005
-- Schema changes needed for bug fixes

-- 1. Add last_fired_at column to alerts table (Finding 10: alert cooldown)
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_fired_at TIMESTAMP WITH TIME ZONE;

-- 2. Create fired_alerts table if not exists (for persisting fired alert records)
CREATE TABLE IF NOT EXISTS fired_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id UUID REFERENCES alerts(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    rule_type VARCHAR(50) NOT NULL,
    trigger_value DECIMAL(20, 2),
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fired_alerts
CREATE INDEX IF NOT EXISTS idx_fired_alerts_alert_id ON fired_alerts(alert_id);
CREATE INDEX IF NOT EXISTS idx_fired_alerts_user_id ON fired_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_fired_alerts_created_at ON fired_alerts(created_at DESC);

-- 3. Add columns to alerts table for per-alert cooldown tracking
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS cooldown_seconds INT DEFAULT 300;

-- 4. Add session_token column to users table for JWT revocation (Finding 11)
-- (already exists in schema, but adding index for lookup performance)
CREATE INDEX IF NOT EXISTS idx_users_session_token ON users(session_token) WHERE session_token IS NOT NULL;

-- 5. Add last_fired_at index for efficient cooldown queries
CREATE INDEX IF NOT EXISTS idx_alerts_last_fired_at ON alerts(last_fired_at) WHERE last_fired_at IS NOT NULL;
