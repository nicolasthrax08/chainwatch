#!/usr/bin/env python3
"""
ChainWatch Migration Status Checker
====================================
Scans the migrations/ directory and cross-references against the database
to determine which migrations have been applied and which are pending.

Each migration file can declare its dependencies and a unique "migration ID"
via special comments. The tool tracks applied migrations in a dedicated
_migration_log table (created automatically if missing).

Usage:
    python check_migration_status.py [--db-url URL] [--migrations-dir DIR] [--check] [--mark-applied ID]

Exit codes:
    0 = all migrations applied (or --check with no pending)
    1 = pending migrations found
    2 = error (DB unreachable, parse failure, etc.)
"""
import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class Migration:
    """Represents a single migration file."""
    file_path: str
    file_name: str
    migration_id: str          # Unique ID extracted from file (or filename-based)
    description: str           # Human-readable description
    depends_on: List[str]      # IDs of migrations that must run first
    is_repeatable: bool        # True for idempotent migrations (CREATE IF NOT EXISTS)
    checksum: str              # Simple hash to detect changes after apply


# ─── Migration ID extraction ────────────────────────────────────────────

def _extract_migration_id(file_name: str, content: str) -> str:
    """
    Extract a unique migration ID from the file.
    Priority:
      1. -- Migration ID: <id> comment
      2. -- ChainWatch Migration <N>  (numbered migrations)
      3. Filename-based (e.g., "001_initial_schema" from "001_initial_schema.sql")
    """
    m = re.search(r'--\s*Migration ID:\s*(\S+)', content, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r'--\s*(?:ChainWatch\s+)?Migration\s+(\d+)', content, re.IGNORECASE)
    if m:
        return f"migration_{m.group(1).zfill(3)}"

    # Fallback: use filename without extension
    base = os.path.splitext(file_name)[0]
    return base


def _extract_description(content: str) -> str:
    """Extract a human-readable description from the migration file header."""
    # Look for first non-empty, non-ID comment line
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith('--'):
            continue
        comment = stripped.lstrip('- ').strip()
        if not comment:
            continue
        # Skip migration ID / number headers
        if re.match(r'(ChainWatch\s+)?Migration\s+\d+', comment, re.IGNORECASE):
            continue
        if comment.startswith('Migration ID:'):
            continue
        if comment.startswith('Depends:'):
            continue
        if comment.startswith('Fix:') or comment.startswith('Adds') or comment.startswith('Add '):
            return comment
        # Return first meaningful comment line
        if len(comment) > 10:
            return comment
    return "(no description)"


def _extract_depends_on(content: str) -> List[str]:
    """Extract dependency IDs from -- Depends: id1, id2, ... comment."""
    m = re.search(r'--\s*Depends:\s*(.+)', content, re.IGNORECASE)
    if m:
        return [d.strip() for d in m.group(1).split(',') if d.strip()]
    return []


def _is_repeatable(content: str) -> bool:
    """
    A migration is considered repeatable if it uses only idempotent operations:
    CREATE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, DO $$ ... END$$ blocks.
    """
    # If the entire content is wrapped in DO $$ ... END $$ (anonymous block), it's repeatable
    if re.search(r'^\s*DO\s+\$\$', content, re.MULTILINE | re.IGNORECASE):
        # Check that it doesn't have non-idempotent operations outside the block
        outside_block = re.sub(r'DO\s+\$\$.*?\$\$\s*;', '', content, flags=re.DOTALL | re.IGNORECASE)
        # Allow CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS outside blocks
        non_idempotent = re.search(
            r'(?<!IF\sNOT\sEXISTS\s)(CREATE\s+(TABLE|INDEX|UNIQUE)|INSERT\s+INTO|DELETE\s+FROM|DROP\s+)',
            outside_block,
            re.IGNORECASE,
        )
        if not non_idempotent:
            return True

    # Check for IF NOT EXISTS patterns throughout
    has_create_table = re.search(r'CREATE\s+TABLE', content, re.IGNORECASE)
    if has_create_table:
        # If CREATE TABLE has IF NOT EXISTS, it's likely repeatable
        if re.search(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS', content, re.IGNORECASE):
            return True
        return False

    # ALTER TABLE ADD COLUMN IF NOT EXISTS is idempotent
    if re.search(r'ALTER\s+TABLE.*ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS', content, re.IGNORECASE):
        return True

    # CREATE INDEX IF NOT EXISTS is idempotent
    if re.search(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS', content, re.IGNORECASE):
        return True

    return False


def _compute_checksum(content: str) -> str:
    """Compute a simple checksum of the migration content (for change detection)."""
    import hashlib
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def parse_migrations(migrations_dir: str) -> List[Migration]:
    """Parse all .sql files in the migrations directory."""
    migrations = []
    sql_files = sorted(
        f for f in os.listdir(migrations_dir) if f.endswith('.sql')
    )

    for fname in sql_files:
        fpath = os.path.join(migrations_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"⚠️  Could not read {fname}: {e}", file=sys.stderr)
            continue

        migration = Migration(
            file_path=fpath,
            file_name=fname,
            migration_id=_extract_migration_id(fname, content),
            description=_extract_description(content),
            depends_on=_extract_depends_on(content),
            is_repeatable=_is_repeatable(content),
            checksum=_compute_checksum(content),
        )
        migrations.append(migration)

    return migrations


# ─── DB interaction (optional — works without DB for dry-run) ────────────

MIGRATION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS _migration_log (
    id SERIAL PRIMARY KEY,
    migration_id VARCHAR(255) UNIQUE NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    checksum VARCHAR(32),
    execution_time_ms INTEGER
);
"""

# Index for efficient lookups
MIGRATION_LOG_INDEX = """
CREATE INDEX IF NOT EXISTS idx_migration_log_id ON _migration_log(migration_id);
"""


async def _ensure_migration_table(db_url: str) -> bool:
    """Create the _migration_log table if it doesn't exist. Returns True on success."""
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(MIGRATION_LOG_DDL)
            await conn.execute(MIGRATION_LOG_INDEX)
            return True
        finally:
            await conn.close()
    except ImportError:
        print("⚠️  asyncpg not installed — cannot connect to DB", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️  DB connection failed: {e}", file=sys.stderr)
        return False


async def _get_applied_migrations(db_url: str) -> dict:
    """
    Fetch applied migrations from _migration_log.
    Returns {migration_id: {file_name, applied_at, checksum}}.
    """
    import asyncpg
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT migration_id, file_name, applied_at, checksum FROM _migration_log ORDER BY applied_at"
        )
        return {
            r["migration_id"]: {
                "file_name": r["file_name"],
                "applied_at": r["applied_at"],
                "checksum": r["checksum"],
            }
            for r in rows
        }
    finally:
        await conn.close()


async def _mark_migration_applied(db_url: str, migration: Migration, execution_time_ms: int = 0) -> bool:
    """Mark a migration as applied in the _migration_log table."""
    import asyncpg
    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                INSERT INTO _migration_log (migration_id, file_name, checksum, execution_time_ms)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (migration_id) DO UPDATE
                    SET file_name = $2, checksum = $3, applied_at = NOW(), execution_time_ms = $4
                """,
                migration.migration_id, migration.file_name, migration.checksum, execution_time_ms,
            )
            return True
        finally:
            await conn.close()
    except Exception as e:
        print(f"⚠️  Failed to mark {migration.migration_id} as applied: {e}", file=sys.stderr)
        return False


# ─── Status computation ─────────────────────────────────────────────────

@dataclass
class MigrationStatus:
    """Status of a single migration."""
    migration: Migration
    status: str  # "applied", "pending", "modified", "missing_dependency"
    applied_at: Optional[str] = None
    current_checksum: Optional[str] = None  # checksum at time of apply
    notes: List[str] = field(default_factory=list)


def compute_statuses(
    migrations: List[Migration],
    applied: dict,
) -> List[MigrationStatus]:
    """
    Compute the status of each migration.
    applied: {migration_id: {file_name, applied_at, checksum}}
    """
    statuses = []
    applied_ids = set(applied.keys())
    migration_ids = {m.migration_id: m for m in migrations}

    for m in migrations:
        mid = m.migration_id

        # Check dependencies
        missing_deps = [d for d in m.depends_on if d not in applied_ids]
        if missing_deps:
            statuses.append(MigrationStatus(
                migration=m,
                status="missing_dependency",
                notes=[f"Missing dependency: {d}" for d in missing_deps],
            ))
            continue

        if mid in applied:
            applied_info = applied[mid]
            if applied_info["checksum"] and applied_info["checksum"] != m.checksum:
                statuses.append(MigrationStatus(
                    migration=m,
                    status="modified",
                    applied_at=str(applied_info["applied_at"]),
                    current_checksum=applied_info["checksum"],
                    notes=[
                        f"Migration was modified after being applied. "
                        f"Original checksum: {applied_info['checksum'][:8]}..., "
                        f"current: {m.checksum[:8]}..."
                    ],
                ))
            else:
                statuses.append(MigrationStatus(
                    migration=m,
                    status="applied",
                    applied_at=str(applied_info["applied_at"]),
                    current_checksum=applied_info["checksum"],
                ))
        else:
            statuses.append(MigrationStatus(
                migration=m,
                status="pending",
                notes=["Not yet applied"] if not m.is_repeatable else ["Not yet applied (idempotent)"],
            ))

    return statuses


# ─── Reporting ──────────────────────────────────────────────────────────

def format_report(statuses: List[MigrationStatus], db_reachable: bool) -> str:
    """Format a human-readable status report."""
    lines = []
    lines.append("=" * 70)
    lines.append("ChainWatch Migration Status Report")
    lines.append("=" * 70)

    if not db_reachable:
        lines.append("\n⚠️  DB unreachable — showing file-based status only (all shown as 'pending')")

    applied = [s for s in statuses if s.status == "applied"]
    pending = [s for s in statuses if s.status == "pending"]
    modified = [s for s in statuses if s.status == "modified"]
    missing_dep = [s for s in statuses if s.status == "missing_dependency"]

    lines.append(f"\nTotal: {len(statuses)} migrations | "
                 f"{len(applied)} applied | {len(pending)} pending | "
                 f"{len(modified)} modified | {len(missing_dep)} blocked")

    if pending:
        lines.append(f"\n📋 PENDING ({len(pending)}):")
        for s in pending:
            repeatable = " [idempotent]" if s.migration.is_repeatable else ""
            lines.append(f"  ⏳ {s.migration.file_name}: {s.migration.description}{repeatable}")
            lines.append(f"     ID: {s.migration.migration_id}")

    if modified:
        lines.append(f"\n🔶 MODIFIED ({len(modified)}):")
        for s in modified:
            lines.append(f"  📝 {s.migration.file_name}: {s.migration.description}")
            lines.append(f"     Applied at: {s.applied_at}")
            for note in s.notes:
                lines.append(f"     ⚠️  {note}")

    if missing_dep:
        lines.append(f"\n🚫 BLOCKED ({len(missing_dep)}):")
        for s in missing_dep:
            lines.append(f"  ⛔ {s.migration.file_name}: {s.migration.description}")
            for note in s.notes:
                lines.append(f"     {note}")

    if applied:
        lines.append(f"\n✅ APPLIED ({len(applied)}):")
        for s in applied:
            lines.append(f"  ✓ {s.migration.file_name}: {s.migration.description}")

    lines.append("\n" + "=" * 70)

    if pending:
        lines.append(f"\nTo apply pending migrations, run each .sql file against your database:")
        for s in pending:
            lines.append(f"  psql $DATABASE_URL -f {s.migration.file_path}")
        lines.append(f"\nOr use: python check_migration_status.py --db-url $DATABASE_URL --apply")

    return "\n".join(lines)


# ─── Main ───────────────────────────────────────────────────────────────

async def run_check(db_url: Optional[str], migrations_dir: str, mark_applied: Optional[str] = None):
    """Run the migration status check."""
    migrations = parse_migrations(migrations_dir)

    if not migrations:
        print("No migration files found in", migrations_dir)
        return 2

    # Try to connect to DB
    db_reachable = False
    applied = {}

    if db_url:
        db_reachable = await _ensure_migration_table(db_url)
        if db_reachable:
            applied = await _get_applied_migrations(db_url)

    # Handle --mark-applied
    if mark_applied and db_reachable and db_url:
        target = next((m for m in migrations if m.migration_id == mark_applied), None)
        if target:
            success = await _mark_migration_applied(db_url, target)
            if success:
                print(f"✅ Marked {mark_applied} as applied")
                # Refresh applied list
                applied = await _get_applied_migrations(db_url)
        else:
            print(f"⚠️  Migration ID '{mark_applied}' not found in {migrations_dir}")
            available = [m.migration_id for m in migrations]
            print(f"   Available IDs: {', '.join(available)}")
            return 2

    statuses = compute_statuses(migrations, applied)

    report = format_report(statuses, db_reachable)
    print(report)

    # Exit code
    pending = [s for s in statuses if s.status == "pending"]
    modified = [s for s in statuses if s.status == "modified"]
    if pending or modified:
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="ChainWatch Migration Status Checker")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL connection string (or set DATABASE_URL env var)",
    )
    parser.add_argument(
        "--migrations-dir",
        default=os.path.join(os.path.dirname(__file__), "migrations"),
        help="Path to migrations directory (default: ../migrations)",
    )
    parser.add_argument(
        "--mark-applied",
        metavar="ID",
        help="Mark a migration as applied (requires --db-url)",
    )
    args = parser.parse_args()

    db_url = args.db_url if args.db_url else None

    import asyncio
    exit_code = asyncio.run(run_check(db_url, args.migrations_dir, args.mark_applied))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
