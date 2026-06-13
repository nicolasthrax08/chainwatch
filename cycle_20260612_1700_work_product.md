# Cycle 2026-06-12 17:00 — Field Contract Fix & Test Coverage Improvement

## Task
Self-generated: `write_tool` — Fix field_contract.py naming mismatch and improve OBJECT_NAME_MAP coverage.

DB unreachable from sandbox (Zeabur internal DNS). No task_queue accessible.

---

## Changes Made

### 1. Fixed field_contract.py naming mismatch (Critical → Minor)

**File:** `backend/services/field_contract.py:218`

The nested dict key `"tier_stats"` was renamed to `"performance_by_tier"` to match the actual backend response field name.

**Before:**
```python
"tier_stats": {"total", "executed", "execution_rate", "avg_confidence", "avg_whale_score"},
```

**After:**
```python
"performance_by_tier": {"total", "executed", "execution_rate", "avg_confidence", "avg_whale_score"},
```

**Impact:** The field contract validator's nested key now matches the actual response shape. Previously, `tier_stats` was dead code — it never matched any real response key, so the nested fields under it were unreachable by the validator.

### 2. Added `stats` to OBJECT_NAME_MAP

**File:** `backend/services/field_contract.py:302-304`

Added `"stats": ["_top_level"]` to `OBJECT_NAME_MAP` so the validator properly checks `stats.*` frontend accesses against endpoint top-level fields.

**Before:** All 19 `stats.*` accesses (from CopyTrades.jsx and Dashboard.jsx) were silently unmatched — the validator couldn't check them at all.

**After:** All 19 `stats.*` accesses are properly matched against the signal_stats endpoint's top-level fields.

### 3. Committed pending changes from previous cycles

- `main.py`: Made `authorization` header Optional with proper 401 response
- `conftest.py`: Added `_restore_main_db_pool` auto-fixture to prevent cross-test contamination
- `test_signal_stats.py`: Fixed test isolation (re-import main, mock require_db, reset db_pool in tearDown)
- `test_main_endpoints.py`: New test file (644 lines) — already tracked, now committed

---

## Verification

```
737 passed, 213 warnings in 24.70s
```

Field contract validation:
```
650 field accesses scanned
0 violations
392 unmatched (local variables — correct)
✅ PASS
```

Stats field matching:
```
stats.total_signals     → MATCHED (19 occurrences)
stats.execution_rate    → MATCHED
stats.avg_confidence    → MATCHED
stats.avg_whale_score   → MATCHED
stats.by_status         → MATCHED
stats.recent_signals    → MATCHED
stats.performance_by_tier → MATCHED
```

---

## Git History

```
527c597 fix(field_contract): rename tier_stats to performance_by_tier, add stats to OBJECT_NAME_MAP
46ab37a feat: raise MIN_WHALE_SCORE to 0.30, add status filter to signal history
e2000e2 Add signal stats endpoint tests (34 tests) + audit fix
```

---

## Recommended Follow-Up Tasks

1. **`write_tool`** (low) — Add `tierData` to OBJECT_NAME_MAP or extend the validator to handle nested-dict-of-dicts patterns (performance_by_tier.tierName.field)
2. **`analyze_failures`** (high) — DB DNS resolution still needs investigation when DB access is available
3. **`audit_outputs`** (medium) — Review the 392 unmatched accesses to see if any should be mapped

---

*Completed by Hermes (solo — peer unavailable: delegate_task requires parent_agent injection from tool context, not available in cron jobs). DB unreachable from sandbox.*
*Next cycle: continue monitoring, consider redeploying if new commits are available.*
