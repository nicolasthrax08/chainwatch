"""
Unit tests for signal tx_hash deduplication (migration 021).
Verifies that the signal generator correctly stores and deduplicates
signals by on-chain transaction hash.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# We test the dedup logic directly since it doesn't require a DB
from services.signal_generator import (
    _is_duplicate,
    _mark_signal,
    _prune_dedup_cache,
    MIN_SIGNAL_USD_BY_CHAIN,
    MIN_WHALE_SCORE,
)


class TestSignalTxHashDedup:
    """Test the tx_hash deduplication logic in signal_generator."""

    def test_dedup_cache_basic(self):
        """_is_duplicate / _mark_signal work for in-memory dedup."""
        _prune_dedup_cache()  # clear state
        assert not _is_duplicate("w1", "ETH", "buy")
        _mark_signal("w1", "ETH", "buy")
        assert _is_duplicate("w1", "ETH", "buy")

    def test_dedup_cache_different_wallet(self):
        """Different wallets are not deduped against each other."""
        _prune_dedup_cache()
        _mark_signal("w1", "ETH", "buy")
        assert not _is_duplicate("w2", "ETH", "buy")

    def test_dedup_cache_different_token(self):
        """Different tokens are not deduped against each other."""
        _prune_dedup_cache()
        _mark_signal("w1", "ETH", "buy")
        assert not _is_duplicate("w1", "SOL", "buy")

    def test_dedup_cache_different_action(self):
        """Different actions are not deduped against each other."""
        _prune_dedup_cache()
        _mark_signal("w1", "ETH", "buy")
        assert not _is_duplicate("w1", "ETH", "receive")

    def test_dedup_cache_case_insensitive_token(self):
        """Token symbol dedup is case-insensitive."""
        _prune_dedup_cache()
        _mark_signal("w1", "eth", "buy")
        assert _is_duplicate("w1", "ETH", "buy")
        assert _is_duplicate("w1", "Eth", "buy")

    def test_dedup_cache_ttl_expiry(self):
        """Entries expire after DEDUP_TTL_SECONDS."""
        _prune_dedup_cache()
        _mark_signal("w1", "ETH", "buy")
        assert _is_duplicate("w1", "ETH", "buy")

        # Manually expire the entry
        import time
        from services.signal_generator import _signal_dedup_cache, _DEDUP_TTL_SECONDS
        key = ("w1", "ETH", "buy")
        if key in _signal_dedup_cache:
            _signal_dedup_cache[key] = time.time() - _DEDUP_TTL_SECONDS - 1

        _prune_dedup_cache()
        assert not _is_duplicate("w1", "ETH", "buy")

    def test_dedup_cache_prune_preserves_recent(self):
        """Pruning preserves recent entries."""
        _prune_dedup_cache()
        _mark_signal("w1", "ETH", "buy")
        _mark_signal("w2", "SOL", "receive")
        _prune_dedup_cache()
        assert _is_duplicate("w1", "ETH", "buy")
        assert _is_duplicate("w2", "SOL", "receive")


class TestSignalThresholds:
    """Test that signal thresholds are sane for current market conditions."""

    def test_btc_min_threshold(self):
        """BTC minimum signal threshold should be >= $10K (dust filter)."""
        assert MIN_SIGNAL_USD_BY_CHAIN["btc"] >= 10000.0

    def test_eth_min_threshold(self):
        """ETH minimum signal threshold should be >= $5K."""
        assert MIN_SIGNAL_USD_BY_CHAIN["eth"] >= 5000.0

    def test_sol_min_threshold(self):
        """SOL minimum signal threshold should be >= $2K."""
        assert MIN_SIGNAL_USD_BY_CHAIN["sol"] >= 2000.0

    def test_whale_score_threshold(self):
        """MIN_WHALE_SCORE should be between 0.2 and 0.5 (not too lenient, not too strict)."""
        assert 0.2 <= MIN_WHALE_SCORE <= 0.5

    def test_default_threshold_fallback(self):
        """Default threshold for unknown chains should be reasonable."""
        from services.signal_generator import MIN_SIGNAL_USD_DEFAULT
        assert MIN_SIGNAL_USD_DEFAULT >= 5000.0


class TestMigration021SQL:
    """Verify migration 021 SQL is well-formed."""

    def test_migration_file_exists(self):
        """Migration 021 should exist in the migrations directory."""
        import os
        migration_path = "migrations/021_add_tx_hash_to_signals.sql"
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"

    def test_migration_adds_column(self):
        """Migration should add tx_hash column."""
        with open("migrations/021_add_tx_hash_to_signals.sql") as f:
            sql = f.read()
        assert "ADD COLUMN IF NOT EXISTS tx_hash" in sql
        assert "TEXT" in sql

    def test_migration_adds_index(self):
        """Migration should add index on tx_hash."""
        with open("migrations/021_add_tx_hash_to_signals.sql") as f:
            sql = f.read()
        assert "idx_copy_trade_signals_tx_hash" in sql
        assert "CREATE INDEX" in sql

    def test_migration_adds_unique_constraint(self):
        """Migration should add unique constraint on (wallet_id, tx_hash)."""
        with open("migrations/021_add_tx_hash_to_signals.sql") as f:
            sql = f.read()
        assert "uq_copy_trade_signals_wallet_tx" in sql
        assert "UNIQUE (wallet_id, tx_hash)" in sql

    def test_migration_uses_idempotent_syntax(self):
        """Migration should use IF NOT EXISTS for idempotency."""
        with open("migrations/021_add_tx_hash_to_signals.sql") as f:
            sql = f.read()
        assert "IF NOT EXISTS" in sql


class TestSignalGeneratorTxHash:
    """Test that signal_generator.py references tx_hash correctly."""

    def test_insert_includes_tx_hash(self):
        """INSERT statement should include tx_hash column."""
        with open("services/signal_generator.py") as f:
            src = f.read()
        assert "tx_hash" in src
        # Check INSERT block
        insert_idx = src.find("INSERT INTO copy_trade_signals")
        assert insert_idx > 0
        insert_block = src[insert_idx:insert_idx + 500]
        assert "tx_hash" in insert_block

    def test_dedup_check_exists(self):
        """signal_generator should check tx_hash for dedup."""
        with open("services/signal_generator.py") as f:
            src = f.read()
        assert "wallet_id = $1 AND tx_hash = $2" in src

    def test_tx_hash_in_signal_dict(self):
        """signal_dict should include tx_hash for WS push."""
        with open("services/signal_generator.py") as f:
            src = f.read()
        assert 'signal_dict["tx_hash"]' in src or "signal_dict['tx_hash']" in src
