-- ChainWatch Migration 010
-- 1. Create _migration_log table (used by check_migration_status.py and /api/health/diagnostic)
-- 2. Add composite index on transactions(wallet_id, timestamp DESC) for activity/dashboard queries
--
-- The _migration_log table is defined in check_migration_status.py as a DDL constant but was
-- never added to the root migrations directory. This means:
--   - check_migration_status.py creates it on first DB connect (via _ensure_migration_table)
--   - /api/health/diagnostic fails to show migration info (the query is wrapped in try/except)
--   - Operators running migrations manually have no migration tracking
--
-- The composite index on (wallet_id, timestamp DESC) covers the JOIN + ORDER BY pattern used
-- in the activity feed and dashboard recent-transactions queries:
--   SELECT t.* FROM transactions t JOIN wallets w ON w.id = t.wallet_id
--   WHERE w.user_id = $1 ORDER BY t.timestamp DESC LIMIT 20
-- Existing separate indexes on wallet_id and timestamp require PostgreSQL to combine them
-- via bitmap scan; the composite index enables a single index range scan.

-- ── 1. Migration log table ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _migration_log (
    id SERIAL PRIMARY KEY,
    migration_id VARCHAR(255) UNIQUE NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    checksum VARCHAR(32),
    execution_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_migration_log_id ON _migration_log(migration_id);
CREATE INDEX IF NOT EXISTS idx_migration_log_applied_at ON _migration_log(applied_at);

-- ── 2. Composite index for activity feed / dashboard tx queries ────────
CREATE INDEX IF NOT EXISTS idx_transactions_wallet_timestamp
    ON transactions(wallet_id, timestamp DESC);
