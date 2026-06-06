# Peer Review Package — Cycle 20260606_0100

## Task
Added `task_queue` table migration and API endpoints to support the Hermes cron job self-improvement loop.

## Files Changed

### 1. `backend/migrations/011_task_queue.sql` (NEW)
Creates the `task_queue` table:
- Columns: id (UUID PK), task_type (VARCHAR), payload (JSONB), status (VARCHAR with CHECK constraint), result (JSONB), critique (JSONB), created_at, updated_at
- Indexes: (status, created_at ASC) for efficient pending-task lookups; partial index on running status
- Trigger: auto-updates `updated_at` on row change

### 2. `migrations/011_task_queue.sql` (NEW)
Root-level mirror of the backend migration (for deployment compatibility).

### 3. `backend/main.py` (MODIFIED)
Added 3 Pydantic models and 4 API endpoints before the health check:

**Models:**
- `TaskCreateRequest(task_type: str, payload: dict)`
- `TaskCompleteRequest(result: dict, critique: dict)`
- `TaskFailRequest(error: str)`

**Endpoints:**
- `GET /api/task-queue/next` — Atomically fetches and locks next pending task using `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`. Returns `{"task": null}` if no pending tasks.
- `POST /api/task-queue/{id}/complete` — Marks running task as done with result + critique JSON.
- `POST /api/task-queue/{id}/fail` — Marks running task as failed with error string.
- `POST /api/task-queue` — Creates new pending task.

## Live Inspection Findings

### Critical
1. **DEPLOY-1**: Deployed code is stale — `/api/whale-suggestions` is unauthenticated in production (returns 200 without Bearer token), but source code has `Depends(get_current_user)`. Container needs redeployment.
2. **DB-1**: Production uses in-memory database (`/api/health` returns `{"database": "in_memory"}`). All data lost on restart. PostgreSQL connection failing.

### Minor
1. **MON-1**: Health endpoint doesn't report monitor status when DB is unavailable.

## Self-Audit Checklist
- Pitfall #19 (ON CONFLICT requires unique constraint): N/A — no ON CONFLICT used
- Pitfall #20 (code refs non-existent columns): All column refs match migration ✓
- Pitfall #6 (try/except import scoping): No new imports in try blocks ✓
- Pitfall #7 (DB conn held across HTTP): Each endpoint does single acquire-use-release ✓
- Pitfall #13/27 (patch tool mangling): Verified surrounding lines after each patch ✓
- Integration: main.py compiles cleanly ✓
- Integration: Migration SQL is valid PostgreSQL ✓
- Integration: FOR UPDATE SKIP LOCKED prevents concurrent double-processing ✓

## Questions for Peer
1. Is the `FOR UPDATE SKIP LOCKED` pattern in `/api/task-queue/next` correct for PostgreSQL? Should we use a CTE instead?
2. Are the task_queue endpoints missing authentication? They're designed to be called from within the Zeabur network only (same as the cron job). Is this acceptable?
3. Should the `payload` column default be `'{}'::jsonb` instead of `'{}'`?
4. Any issues with the migration being in both `backend/migrations/` and `migrations/` directories?
