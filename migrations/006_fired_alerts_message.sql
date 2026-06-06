-- ChainWatch Migration 006
-- Adds missing message column to fired_alerts table
-- Required by alert_evaluator.py which references this column in INSERT statements
-- Run after 005_consolidated_backend_parity.sql

ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS message TEXT DEFAULT '';
