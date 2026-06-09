-- ChainWatch Migration 011
-- Add Telegram notification support to alerts
--
-- 1. Add telegram_chat_id column to users table (nullable; only users who
--    want Telegram notifications need to set it)
-- 2. Add notify_telegram column to alerts table (default TRUE for new alerts)
--    so users can opt-in per-alert

-- ── 1. User Telegram chat ID ──────────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS telegram_chat_id VARCHAR(64) DEFAULT NULL;

-- ── 2. Alert Telegram notification flag ───────────────────────────────
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS notify_telegram BOOLEAN DEFAULT TRUE;

-- ── 3. Index for users with Telegram configured ───────────────────────
CREATE INDEX IF NOT EXISTS idx_users_telegram_chat_id
    ON users(telegram_chat_id)
    WHERE telegram_chat_id IS NOT NULL;
