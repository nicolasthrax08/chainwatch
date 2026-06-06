-- ChainWatch Migration 009
-- Fix: Add `details` column to fired_alerts table
--
-- The alert_evaluator.py INSERT INTO fired_alerts (alert_id, user_id, rule_type, trigger_value, details)
-- references a `details` column that doesn't exist in the schema.
-- Without this column, every alert fire raises UndefinedColumnError at runtime.
-- This is a Pitfall #20 finding: code references DB columns that don't exist in the schema.

ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS details JSONB DEFAULT '{}';

-- Add `message` column for the alert message text (currently only passed in-memory
-- between evaluate_alerts() and the WS push in monitor.py, but useful for history display)
ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS message TEXT DEFAULT '';
