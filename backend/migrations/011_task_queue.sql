-- ChainWatch Migration 011
-- Adds task_queue table for Hermes cron job self-improvement loop.
-- This table is the persistent memory for the cron agent across cycles.
-- Each row represents a unit of work: pending → running → done/failed.

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

-- Index for efficient pending-task lookups (the cron agent's primary query)
CREATE INDEX IF NOT EXISTS idx_task_queue_status_created
    ON task_queue(status, created_at ASC);

-- Index for monitoring running tasks (detect stale/orphaned)
CREATE INDEX IF NOT EXISTS idx_task_queue_running
    ON task_queue(status) WHERE status = 'running';

-- Auto-update updated_at on row change
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
