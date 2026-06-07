-- ChainWatch Migration 008
-- Consolidates backend/migrations/ 008, 009, 010, 011, 012 into root migrations/.
-- This ensures that running root migrations/* produces the same schema as backend/migrations/*.
--
-- Missing from root (consolidated here):
--   - fired_alerts unique constraint + index (from backend 008)
--   - fired_alerts.details column (from backend 009)
--   - copy_trade_signals dedup unique constraint + index (from backend 010)
--   - task_queue table + indexes + trigger (from backend 011)
--   - wallets.balance_hkd/btc backfill (from backend 012, supersedes root 007)

-- ── 1. fired_alerts: add details column if missing (backend 009) ──────
ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS details JSONB DEFAULT '{}';
ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS message TEXT DEFAULT '';

-- ── 2. fired_alerts: unique constraint for ON CONFLICT DO NOTHING (backend 008) ──
-- Required by alert_evaluator.py line 292: ON CONFLICT DO NOTHING
-- Without this, duplicate fired_alerts rows are silently inserted.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fired_alerts_alert_trigger'
    ) THEN
        ALTER TABLE fired_alerts ADD CONSTRAINT uq_fired_alerts_alert_trigger
            UNIQUE (alert_id, trigger_value);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_fired_alerts_alert_id_trigger
    ON fired_alerts(alert_id, trigger_value);

-- ── 3. copy_trade_signals: dedup unique constraint (backend 010) ──────
-- Required by signal_generator.py line 247:
--   ON CONFLICT (wallet_id, token_symbol, action, amount_usd) DO NOTHING
-- Without this, duplicate signals are silently inserted every poll cycle.
--
-- First, clean up any existing duplicates (keep the earliest)
DELETE FROM copy_trade_signals a USING copy_trade_signals b
WHERE a.id > b.id
  AND a.wallet_id = b.wallet_id
  AND a.token_symbol = b.token_symbol
  AND a.action = b.action
  AND a.amount_usd = b.amount_usd;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_copy_trade_signals_dedup'
    ) THEN
        ALTER TABLE copy_trade_signals ADD CONSTRAINT uq_copy_trade_signals_dedup
            UNIQUE (wallet_id, token_symbol, action, amount_usd);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_action
    ON copy_trade_signals(wallet_id, action);

-- ── 4. task_queue table (backend 011) ─────────────────────────────────
-- Persistent work queue for Hermes cron self-improvement loop.
CREATE TABLE IF NOT EXISTS task_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_type VARCHAR(50) NOT NULL,
    payload JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    result JSONB DEFAULT NULL,
    critique JSONB DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_queue_status_created
    ON task_queue(status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_task_queue_running
    ON task_queue(status) WHERE status = 'running';

CREATE OR REPLACE FUNCTION update_task_queue_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_task_queue_updated_at ON task_queue;
CREATE TRIGGER trg_task_queue_updated_at
    BEFORE UPDATE ON task_queue
    FOR EACH ROW
    EXECUTE FUNCTION update_task_queue_updated_at();

-- ── 5. wallets: backfill balance_hkd/btc from balance_usd (backend 012) ──
-- Ensures existing wallets show correct converted balances immediately.
-- Rates: USDHKD ≈ 7.8, USDBTC ≈ 1/62000
UPDATE wallets
SET balance_hkd = COALESCE(balance_usd, 0) * 7.8,
    balance_btc = COALESCE(balance_usd, 0) / 62000.0
WHERE balance_hkd = 0 AND balance_btc = 0 AND balance_usd IS NOT NULL;
