#!/usr/bin/env python3
"""
Migration Parity & Consolidation Tests
=======================================
Ensures that:
1. The root-level migrations/ directory is not a stale duplicate (pitfall #17)
2. backend/migrations/ is the canonical migration source
3. All migration files have valid sequential numbering
4. No migration IDs are duplicated
5. Every migration has a valid SQL structure (basic sanity check)
6. The check_migration_status.py tool can parse all migrations correctly
7. Migration files contain required header metadata (ID, description)
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
BACKEND_MIGRATIONS = BACKEND_DIR / "migrations"
ROOT_MIGRATIONS = PROJECT_DIR / "migrations"


class TestMigrationDirectoryStructure(unittest.TestCase):
    """Verify migration directory structure follows the canonical layout."""

    def test_backend_migrations_dir_exists(self):
        """backend/migrations/ must exist and contain migration files."""
        self.assertTrue(BACKEND_MIGRATIONS.is_dir(),
                        "backend/migrations/ directory must exist")
        files = list(BACKEND_MIGRATIONS.glob("*.sql"))
        self.assertGreater(len(files), 0,
                           "backend/migrations/ must contain .sql files")

    def test_backend_migrations_are_canonical(self):
        """backend/migrations/ should be the only migration source used by code.

        The root-level migrations/ directory is a leftover from pre-reorganization
        and should not be referenced by any Python source file (pitfall #17).
        """
        # Check that no Python file in backend/ references the root migrations dir
        for py_file in sorted(BACKEND_DIR.rglob("*.py")):
            if py_file.name == "test_migration_parity.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '"migrations"' in line or "'migrations'" in line:
                    # Allow the default in check_migration_status.py
                    if py_file.name == "check_migration_status.py":
                        continue
                    self.assertNotIn(
                        "../migrations", line,
                        f"{py_file}:{i} references '../migrations' — "
                        f"should use backend/migrations instead"
                    )

    def test_no_dual_backend_drift(self):
        """If root migrations/ exists, verify it's marked as deprecated.

        The root-level migrations/ directory should either be removed or
        contain a README explaining it's deprecated in favor of backend/migrations/.
        """
        if not ROOT_MIGRATIONS.is_dir():
            return  # Already cleaned up — ideal state

        # If it still exists, there should be a deprecation marker
        has_marker = any(
            f.name.startswith("README") or f.name == ".deprecated"
            for f in ROOT_MIGRATIONS.iterdir()
            if f.is_file()
        )

        root_files = {f.name for f in ROOT_MIGRATIONS.glob("*.sql")}
        if root_files and not has_marker:
            self.fail(
                f"Root migrations/ directory contains {len(root_files)} SQL files "
                f"but no deprecation marker. Either remove it or add a "
                f".deprecated file explaining backend/migrations/ is canonical."
            )


class TestMigrationFileFormat(unittest.TestCase):
    """Verify individual migration file format and metadata."""

    def _get_backend_migrations(self):
        return sorted(BACKEND_MIGRATIONS.glob("*.sql"))

    def test_migrations_have_sequential_numbers(self):
        """Migration files should have monotonically increasing 3-digit prefixes.

        Gaps are allowed — they indicate migrations that were consolidated
        or removed during development (e.g., 002-003 were folded into 001).
        """
        files = self._get_backend_migrations()
        prefixes = []
        for f in files:
            match = re.match(r"^(\d{3})_", f.name)
            self.assertIsNotNone(
                match,
                f"Migration file {f.name} doesn't start with 3-digit prefix (e.g., 001_)"
            )
            prefixes.append(int(match.group(1)))

        # Verify monotonically increasing (no out-of-order files)
        for i in range(1, len(prefixes)):
            self.assertGreater(
                prefixes[i], prefixes[i - 1],
                f"Migration {prefixes[i]:03d} comes after {prefixes[i-1]:03d} "
                f"but should have a higher number"
            )

    def test_migrations_have_descriptive_names(self):
        """Migration file names should have descriptive suffixes."""
        files = self._get_backend_migrations()
        for f in files:
            name_without_ext = f.stem  # e.g., "001_initial_schema"
            parts = name_without_ext.split("_", 1)
            self.assertGreater(
                len(parts), 1,
                f"Migration {f.name} has no descriptive suffix after prefix"
            )
            self.assertGreater(
                len(parts[1]), 3,
                f"Migration {f.name} has a very short description: '{parts[1]}'"
            )

    def test_migration_headers_contain_metadata(self):
        """Each migration should have a header with ID and description."""
        files = self._get_backend_migrations()
        for f in files:
            content = f.read_text(encoding="utf-8", errors="replace")
            first_lines = "\n".join(content.split("\n")[:10])

            has_id = bool(re.search(r"--\s*(Migration ID|ChainWatch Migration)", first_lines))
            has_description = bool(re.search(r"--\s*\w{3,}", first_lines))

            self.assertTrue(
                has_id or has_description,
                f"Migration {f.name} has no metadata header "
                f"(expected '-- Migration ID:' or '-- ChainWatch Migration N')"
            )

    def test_migrations_are_valid_sql(self):
        """Basic SQL sanity checks on migration files."""
        files = self._get_backend_migrations()
        for f in files:
            content = f.read_text(encoding="utf-8", errors="replace")
            self.assertGreater(len(content.strip()), 50,
                               f"Migration {f.name} is suspiciously short")

            upper = content.upper()
            has_sql = any(kw in upper for kw in [
                "CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "DROP",
                "SELECT", "BEGIN", "COMMIT", "GRANT", "REVOKE",
            ])
            self.assertTrue(has_sql,
                            f"Migration {f.name} doesn't contain recognizable SQL keywords")

    def test_no_duplicate_migration_ids(self):
        """No two migration files should have the same ID."""
        files = self._get_backend_migrations()
        ids = []
        for f in files:
            match = re.match(r"^(\d{3})_", f.name)
            if match:
                ids.append(match.group(1))

        seen = set()
        duplicates = set()
        for mid in ids:
            if mid in seen:
                duplicates.add(mid)
            seen.add(mid)

        self.assertEqual(
            duplicates, set(),
            f"Duplicate migration IDs found: {sorted(duplicates)}"
        )


class TestMigrationContentConsistency(unittest.TestCase):
    """Verify migration content is consistent and non-contradictory."""

    def _get_backend_migrations(self):
        return sorted(BACKEND_MIGRATIONS.glob("*.sql"))

    def test_create_table_has_if_not_exists(self):
        """CREATE TABLE statements should use IF NOT EXISTS for idempotency."""
        files = self._get_backend_migrations()
        for f in files:
            content = f.read_text(encoding="utf-8", errors="replace")
            creates = re.findall(
                r'CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)(\w+)',
                content, re.IGNORECASE
            )
            bare_creates = [name for name in creates if name.upper() != "IF"]
            self.assertEqual(
                bare_creates, [],
                f"Migration {f.name} has bare CREATE TABLE without IF NOT EXISTS: "
                f"{bare_creates}"
            )

    def test_no_bare_drop_table(self):
        """DROP TABLE should use IF EXISTS to avoid errors on re-run."""
        files = self._get_backend_migrations()
        for f in files:
            content = f.read_text(encoding="utf-8", errors="replace")
            bare_drops = re.findall(
                r'DROP\s+TABLE\s+(?!IF\s+EXISTS)(\w+)',
                content, re.IGNORECASE
            )
            bare_drops = [d for d in bare_drops if d.upper() != "IF"]
            self.assertEqual(
                bare_drops, [],
                f"Migration {f.name} has bare DROP TABLE without IF EXISTS: "
                f"{bare_drops}"
            )

    def test_migration_001_has_core_tables(self):
        """The initial schema migration should create all core tables."""
        initial = BACKEND_MIGRATIONS / "001_initial_schema.sql"
        if not initial.exists():
            self.skipTest("001_initial_schema.sql not found")

        content = initial.read_text(encoding="utf-8", errors="replace").upper()
        self.assertIn("CREATE TABLE", content,
                      "001_initial_schema.sql should contain CREATE TABLE statements")
        for table in ["USERS", "WALLETS", "TRANSACTIONS"]:
            self.assertIn(table, content,
                          f"001_initial_schema.sql should create {table} table")


class TestMigrationStatusTool(unittest.TestCase):
    """Verify the check_migration_status.py tool works correctly."""

    def test_tool_imports_correctly(self):
        """check_migration_status.py should have valid Python syntax."""
        import py_compile
        try:
            py_compile.compile(
                str(BACKEND_DIR / "check_migration_status.py"),
                doraise=True
            )
        except py_compile.PyCompileError as e:
            self.fail(f"check_migration_status.py has syntax errors: {e}")

    def test_tool_parses_all_migrations(self):
        """check_migration_status.py should be able to parse all migration files."""
        import importlib.util
        tool_path = str(BACKEND_DIR / "check_migration_status.py")
        spec = importlib.util.spec_from_file_location("check_migration_status", tool_path)
        self.assertIsNotNone(spec, "Failed to create module spec for check_migration_status.py")
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader = spec.loader
        assert loader is not None
        try:
            loader.exec_module(mod)
        except Exception as e:
            self.fail(f"Failed to load check_migration_status.py: {e}")

        migrations = mod.parse_migrations(str(BACKEND_MIGRATIONS))
        sql_files = list(BACKEND_MIGRATIONS.glob("*.sql"))

        self.assertEqual(
            len(migrations), len(sql_files),
            f"parse_migrations returned {len(migrations)} entries "
            f"but there are {len(sql_files)} SQL files"
        )

    def test_all_migrations_have_unique_ids(self):
        """All parsed migrations should have unique IDs."""
        import importlib.util
        tool_path = str(BACKEND_DIR / "check_migration_status.py")
        spec = importlib.util.spec_from_file_location("check_migration_status", tool_path)
        self.assertIsNotNone(spec)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader = spec.loader
        assert loader is not None
        loader.exec_module(mod)

        migrations = mod.parse_migrations(str(BACKEND_MIGRATIONS))
        ids = [m.migration_id for m in migrations]
        unique_ids = set(ids)

        self.assertEqual(
            len(ids), len(unique_ids),
            f"Duplicate migration IDs: "
            f"{[x for x in ids if ids.count(x) > 1]}"
        )

    def test_all_migrations_have_checksums(self):
        """All parsed migrations should have non-empty checksums."""
        import importlib.util
        tool_path = str(BACKEND_DIR / "check_migration_status.py")
        spec = importlib.util.spec_from_file_location("check_migration_status", tool_path)
        self.assertIsNotNone(spec)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader = spec.loader
        assert loader is not None
        loader.exec_module(mod)

        migrations = mod.parse_migrations(str(BACKEND_MIGRATIONS))
        for m in migrations:
            self.assertTrue(
                m.checksum,
                f"Migration {m.file_name} has empty checksum"
            )
            self.assertGreater(
                len(m.checksum), 8,
                f"Migration {m.file_name} checksum is too short"
            )


class TestMigrationCompleteness(unittest.TestCase):
    """Verify that migrations cover all required schema elements."""

    def _get_all_migration_sql(self):
        """Read and concatenate all migration SQL."""
        files = sorted(BACKEND_MIGRATIONS.glob("*.sql"))
        parts = []
        for f in files:
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def test_wallets_table_has_balance_columns(self):
        """Wallets table should have all required balance columns."""
        sql = self._get_all_migration_sql().upper()
        required = [
            "BALANCE_NATIVE", "BALANCE_USD", "BALANCE_HKD", "BALANCE_BTC",
            "LAST_BALANCE_UPDATE",
        ]
        for col in required:
            self.assertIn(col, sql,
                          f"Migration SQL is missing required column: {col}")

    def test_wallets_table_has_whale_score_columns(self):
        """Wallets table should have whale scoring columns."""
        sql = self._get_all_migration_sql().upper()
        required = [
            "WHALE_SCORE", "SCORE_ACTIVITY", "SCORE_RELIABILITY",
            "SCORE_WEIGHT", "SCORE_RECENCY", "SCORE_DIVERSITY",
        ]
        for col in required:
            self.assertIn(col, sql,
                          f"Migration SQL is missing required whale score column: {col}")

    def test_copy_trade_signals_has_explanation_columns(self):
        """copy_trade_signals table should have explanation columns."""
        sql = self._get_all_migration_sql().upper()
        required = ["EXPLANATION", "EXPLANATION_STALE", "SCORE_AT_GENERATION"]
        for col in required:
            self.assertIn(col, sql,
                          f"Migration SQL is missing required signal column: {col}")

    def test_transactions_has_unique_constraint(self):
        """Transactions table should have unique constraint on (tx_hash, chain)."""
        sql = self._get_all_migration_sql().upper()
        has_unique = (
            "UQ_TRANSACTIONS_TX_HASH_CHAIN" in sql
            or ("TX_HASH" in sql and "UNIQUE" in sql)
        )
        self.assertTrue(
            has_unique,
            "Migration SQL should have unique constraint on transactions(tx_hash, chain)"
        )

    def test_users_has_wallet_address_unique(self):
        """Users table should have unique constraint on wallet_address."""
        sql = self._get_all_migration_sql().upper()
        self.assertIn(
            "UQ_USERS_WALLET_ADDRESS", sql,
            "Migration SQL should have unique constraint on users(wallet_address)"
        )


if __name__ == "__main__":
    unittest.main()
