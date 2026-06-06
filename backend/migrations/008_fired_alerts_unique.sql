-- ChainWatch Migration 008
-- Fix: Add unique constraint on fired_alerts(alert_id, user_id, rule_type, trigger_value)
-- so that ON CONFLICT DO NOTHING in alert_evaluator.py line 231 works correctly.
--
-- Without this, the same alert can be recorded multiple times per poll cycle.
-- The combination (alert_id, user_id, rule_type, trigger_value) deduplicates
-- repeated firings of the same alert for the same trigger value within a cycle.
--
-- Also adds is_mine column read to monitor changed_wallets tuple (code-level fix).

-- Add composite unique constraint on fired_alerts to prevent duplicate alert records
-- The natural dedup key is (alert_id, trigger_value) — same alert shouldn't fire
-- with the exact same trigger_value twice (the cooldown prevents it anyway,
-- but on restart or cooldown expiry we want DB-level protection).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fired_alerts_alert_trigger'
    ) THEN
        ALTER TABLE fired_alerts ADD CONSTRAINT uq_fired_alerts_alert_trigger
            UNIQUE (alert_id, trigger_value);
    END IF;
END$$;

-- Add index for efficient alert cooldown lookups
CREATE INDEX IF NOT EXISTS idx_fired_alerts_alert_id_trigger
    ON fired_alerts(alert_id, trigger_value);
