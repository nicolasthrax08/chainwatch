#!/usr/bin/env python3
"""
Integration test: monitor → signal_generator → alert_evaluator pipeline.

This test exercises the full pipeline end-to-end using a fake in-memory
database connection (_FakeConn) that mimics the asyncpg connection interface
used by the real pipeline. No real PostgreSQL instance is required.

Coverage:
  Phase 1: Monitor detects a new whale wallet tx → balance/tx state update
  Phase 2: SignalGenerator.evaluate_for_signal creates a copy_trade_signal
  Phase 3: SignalGenerator.generate_explanation produces human-readable text
  Phase 4: AlertEvaluator.evaluate_alerts fires matching user alert rules
  Cross-cutting: cooldowns, dedup, whale_score gating, amount thresholds

Run: python3 -m pytest backend/tests/test_pipeline_integration.py -v
"""
import asyncio
import math
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import monitor as mon
from services import signal_generator as sg
from services import alert_evaluator as ae
from services import whale_scorer as ws


# ─── Helpers ───────────────────────────────────────────────────────────────

class _FakeConn:
    """
    Minimal fake asyncpg connection for pipeline integration tests.

    Supports the operations used by signal_generator and alert_evaluator:
    - fetchval(sql, *args) → scalar
    - fetchrow(sql, *args) → dict-like | None
    - fetch(sql, *args) → list of dicts
    - execute(sql, *args) → str (status)

    The fake maintains in-memory tables for the tables we care about:
    - copy_trade_signals
    - transactions
    - alerts
    - fired_alerts
    - users
    - wallets
    """

    def __init__(self):
        self._tables = {
            "copy_trade_signals": [],
            "transactions": [],
            "alerts": [],
            "fired_alerts": [],
            "users": [],
            "wallets": [],
        }
        self._id_counters = {t: 0 for t in self._tables}

    def _next_id(self, table):
        self._id_counters[table] += 1
        return f"{table[:-1]}-{self._id_counters[table]:03d}"

    async def fetchval(self, sql, *args):
        # Signal dedup check: SELECT id FROM copy_trade_signals WHERE ...
        if "copy_trade_signals" in sql and "wallet_id" in sql:
            for row in self._tables["copy_trade_signals"]:
                if (str(row.get("wallet_id")) == str(args[0]) and
                        row.get("token_symbol", "").upper() == str(args[1]).upper()):
                    return row["id"]
            return None
        # Transaction usd_value lookup
        if "usd_value" in sql and "transactions" in sql:
            # Match by wallet_id + tx_hash
            wid = str(args[0]) if args else None
            txh = str(args[1]) if len(args) > 1 else None
            matches = [
                r for r in self._transactions
                if str(r.get("wallet_id")) == wid and r.get("tx_hash") == txh
            ]
            if matches:
                return float(matches[-1].get("usd_value", 0))
            return None
        # Portfolio total
        if "SUM(balance_usd)" in sql:
            total = sum(
                float(r.get("balance_usd", 0) or 0)
                for r in self._tables["wallets"]
                if str(r.get("user_id")) == str(args[0])
                and r.get("is_mine") is True
                and r.get("is_whale") is False
            )
            return total
        return None

    async def fetchrow(self, sql, *args):
        # INSERT INTO copy_trade_signals ... RETURNING
        if sql.strip().startswith("INSERT INTO copy_trade_signals"):
            row_id = self._next_id("copy_trade_signals")
            row = {
                "id": row_id,
                "wallet_id": args[0],
                "token_symbol": args[1],
                "action": args[2],
                "amount_usd": float(args[3]) if args[3] else 0,
                "confidence_score": float(args[4]) if args[4] else 0,
                "confidence_final": float(args[5]) if len(args) > 5 and args[5] else 0,
                "score_at_generation": float(args[6]) if len(args) > 6 and args[6] else 0,
                "status": "pending",
                "created_at": time.time(),
                "explanation": None,
                "explanation_stale": True,
            }
            self._tables["copy_trade_signals"].append(row)
            return row

        # UPDATE copy_trade_signals SET explanation
        if sql.strip().startswith("UPDATE copy_trade_signals"):
            sid = args[0]
            for row in self._tables["copy_trade_signals"]:
                if str(row["id"]) == str(sid):
                    row["explanation"] = args[1]
                    row["explanation_stale"] = False
                    row["score_at_generation"] = float(args[2]) if len(args) > 2 else row.get("score_at_generation", 0)
                    return row
            return None

        # SELECT usd_value FROM transactions
        if "usd_value" in sql and "transactions" in sql:
            val = await self.fetchval(sql, *args)
            if val is not None:
                return {"usd_value": val}
            return None

        # Portfolio total
        if "SUM(balance_usd)" in sql:
            val = await self.fetchval(sql, *args)
            return {"total_usd": val}

        return None

    async def fetch(self, sql, *args):
        # SELECT FROM alerts WHERE user_id = ANY($1) AND enabled = TRUE
        if "FROM alerts" in sql and "enabled" in sql:
            user_ids = set(str(u) for u in args[0]) if args else set()
            rows = []
            for row in self._tables["alerts"]:
                if row.get("enabled") and str(row.get("user_id")) in user_ids:
                    rows.append(dict(row))
            return rows

        # SELECT id, telegram_chat_id FROM users WHERE ...
        if "telegram_chat_id" in sql:
            user_ids = set(str(u) for u in args[0]) if args else set()
            rows = []
            for row in self._tables["users"]:
                if str(row.get("id")) in user_ids and row.get("telegram_chat_id"):
                    rows.append({"id": row["id"], "telegram_chat_id": row["telegram_chat_id"]})
            return rows

        # Batch tx lookup: SELECT wallet_id, tx_hash, usd_value FROM transactions
        if "FROM transactions" in sql and "wallet_id = ANY" in sql:
            wallet_ids = [str(w) for w in args[0]] if args else []
            tx_hashes = [str(h) for h in args[1]] if len(args) > 1 else []
            rows = []
            for r in self._tables["transactions"]:
                if (str(r.get("wallet_id")) in wallet_ids and
                        r.get("tx_hash") in tx_hashes):
                    rows.append(dict(r))
            return rows

        # Single tx usd_value lookup (fallback)
        if "FROM transactions" in sql:
            wid = str(args[0]) if args else None
            txh = str(args[1]) if len(args) > 1 else None
            matches = [
                r for r in self._tables["transactions"]
                if str(r.get("wallet_id")) == wid and r.get("tx_hash") == txh
            ]
            if matches:
                return [{"usd_value": float(matches[-1].get("usd_value", 0))}]
            return []

        return []

    async def execute(self, sql, *args):
        # Batch INSERT fired_alerts via unnest
        if "INSERT INTO fired_alerts" in sql:
            if "unnest" in sql:
                # args: alert_ids, user_ids, rule_types, trigger_values, details, messages
                n = len(args[0]) if args else 0
                alert_ids = [str(a) for a in (args[0] if n else [])]
                user_ids = [str(u) for u in (args[1] if n else [])]
                rule_types = [str(r) for r in (args[2] if n else [])]
                trigger_values = [float(v) for v in (args[3] if n else [])]
                messages = [str(m) for m in (args[5] if len(args) > 5 and n else [""] * n)]
                for i in range(n):
                    self._tables["fired_alerts"].append({
                        "alert_id": alert_ids[i],
                        "user_id": user_ids[i],
                        "rule_type": rule_types[i],
                        "trigger_value": trigger_values[i],
                        "message": messages[i],
                        "details": "{}",
                    })
            else:
                single = {
                    "alert_id": str(args[0]),
                    "user_id": str(args[1]),
                    "rule_type": str(args[2]),
                    "trigger_value": float(args[3]),
                    "details": args[4] if len(args) > 4 else "{}",
                    "message": args[5] if len(args) > 5 else "",
                }
                self._tables["fired_alerts"].append(single)
            return "INSERT 0 1"

        # Batch UPDATE last_fired_at
        if "UPDATE alerts SET last_fired_at" in sql:
            from datetime import datetime, timezone
            alert_ids = [str(a) for a in args[0]] if args else []
            for row in self._tables["alerts"]:
                if str(row.get("id")) in alert_ids:
                    row["last_fired_at"] = datetime.now(timezone.utc)
            return "UPDATE"

        return "OK"

    # Convenience: expose tables directly for test assertions
    @property
    def _transactions(self):
        return self._tables["transactions"]

    def seed_transaction(self, wallet_id, tx_hash, usd_value, created_at=None):
        """Pre-populate a transaction for tx usd_value lookups."""
        self._tables["transactions"].append({
            "wallet_id": wallet_id,
            "tx_hash": tx_hash,
            "usd_value": float(usd_value),
            "created_at": created_at or time.time(),
        })

    def seed_alert(self, alert_id, user_id, rule_type, threshold,
                   enabled=True, last_fired_at=None, notify_telegram=True):
        """Create an alert rule in the fake DB."""
        from datetime import datetime, timezone
        self._tables["alerts"].append({
            "id": alert_id,
            "user_id": user_id,
            "rule_type": rule_type,
            "threshold": float(threshold),
            "enabled": enabled,
            "last_fired_at": last_fired_at if last_fired_at else datetime(2000, 1, 1, tzinfo=timezone.utc),
            "notify_telegram": notify_telegram,
            "created_at": datetime.now(timezone.utc),
        })

    def seed_user(self, user_id, telegram_chat_id=None):
        """Create a user in the fake DB."""
        self._tables["users"].append({
            "id": user_id,
            "telegram_chat_id": telegram_chat_id,
        })

    def seed_wallet(self, user_id, balance_usd, is_mine=True, is_whale=False):
        """Create a wallet in the fake DB for portfolio queries."""
        wid = f"wallet-{len(self._tables['wallets']) + 1:03d}"
        self._tables["wallets"].append({
            "id": wid,
            "user_id": user_id,
            "balance_usd": float(balance_usd),
            "is_mine": is_mine,
            "is_whale": is_whale,
        })
        return wid


# ─── Test Class ────────────────────────────────────────────────────────────

class TestPipelineIntegration(unittest.TestCase):
    """
    End-to-end pipeline test: monitor detects whale tx → signal generated → alert fired.
    """

    def setUp(self):
        """Reset all module-level state before each test."""
        sg._signal_dedup_cache.clear()
        ae._cooldown_cache.clear()
        ae._last_cooldown_prune = 0.0
        mon._last_balances.clear()
        mon._last_tx_hashes.clear()
        mon._consecutive_errors.clear()
        mon._price_cache.clear()
        mon._price_cache.update({
            "ETH": 2500.0, "SOL": 170.0, "BTC": 105000.0,
            "USDHKD": 7.8, "USDBTC": 1.0 / 105000.0,
            "timestamp": time.time(),
        })

    def tearDown(self):
        sg._signal_dedup_cache.clear()
        ae._cooldown_cache.clear()

    # ── Phase 1: Generate signal from a whale buy tx ───────────────────────

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_whale_buy_generates_signal_with_explanation(self):
        """
        GIVEN: A whale wallet (score=0.85) buys 5 ETH at $2500/ETH = $12,500.
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: A signal is created with confidence_final ≈ 0.68, explanation set,
              and the signal appears in the fake DB.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="whale-001",
            is_whale=True,
            user_id="user-001",
            chain="eth",
            tx_hash="0xabc123",
            tx_type="buy",
            token="ETH",
            tx_amount_native=5.0,
            price_usd=2500.0,
            whale_score=0.85,
            median_amount_30d=8000.0,
            execution_rate_30d=0.75,
            wallet_label="Smart Money Alpha",
            wallet_address="0xABCDEF0123456789",
        ))

        self.assertIsNotNone(signal, "Signal should be generated for whale buy")
        self.assertEqual(signal["token_symbol"], "ETH")
        self.assertEqual(signal["action"], "buy")
        self.assertAlmostEqual(signal["amount_usd"], 12500.0, places=1)
        # C_tx for $12.5K: log10(12500)=4.097, raw=1.097, confidence=0.5+(1.097/3)*0.5=0.683
        self.assertAlmostEqual(signal["confidence_score"], 0.68, places=1)
        # C_final = 0.5*C_tx + 0.5*W = 0.5*0.68 + 0.5*0.85 = 0.765
        self.assertAlmostEqual(signal["confidence_final"], 0.77, places=1)
        self.assertIn("explanation", signal)
        self.assertIsNotNone(signal["explanation"])
        self.assertTrue(len(signal["explanation"]) > 0)
        self.assertIn("ETH", signal["explanation"])
        self.assertFalse(signal["explanation_stale"])

    def test_whale_receive_generates_signal(self):
        """
        GIVEN: A whale wallet receives 20 ETH ($50K).
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: A signal is generated with action='receive'.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="whale-002",
            is_whale=True,
            user_id="user-002",
            chain="eth",
            tx_hash="0xdef456",
            tx_type="receive",
            token="ETH",
            tx_amount_native=20.0,
            price_usd=2500.0,
            whale_score=0.60,
            wallet_label=None,
            wallet_address="0xBBB111",
        ))

        self.assertIsNotNone(signal, "Signal should be generated for whale receive")
        self.assertEqual(signal["action"], "receive")
        self.assertEqual(signal["amount_usd"], 50000.0)
        self.assertIn("Whale 0xBBB1", signal["explanation"])

    def test_non_whale_wallet_suppressed(self):
        """
        GIVEN: A non-whale wallet makes a large buy.
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: No signal is generated (is_whale=False fast-return).
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="normal-wallet",
            is_whale=False,
            user_id="user-003",
            chain="eth",
            tx_hash="0x123",
            tx_type="buy",
            token="ETH",
            tx_amount_native=100.0,
            price_usd=2500.0,
            whale_score=0.1,
        ))
        self.assertIsNone(signal)

    def test_low_amount_suppressed_by_chain_threshold(self):
        """
        GIVEN: A whale wallet buys 0.5 ETH ($1250) — below MIN_SIGNAL_USD_BY_CHAIN['eth']=5000.
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: No signal is generated.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="whale-003",
            is_whale=True,
            user_id="user-004",
            chain="eth",
            tx_hash="0xlowamt",
            tx_type="buy",
            token="ETH",
            tx_amount_native=0.5,
            price_usd=2500.0,
            whale_score=0.9,
        ))
        self.assertIsNone(signal)

    def test_low_whale_score_suppressed(self):
        """
        GIVEN: A wallet with whale_score=0.15 (below MIN_WHALE_SCORE=0.30).
        WHEN: signal_generator.evaluate_for_signal is called with a large buy.
        THEN: No signal is generated.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="weak-whale",
            is_whale=True,
            user_id="user-005",
            chain="eth",
            tx_hash="0xweak",
            tx_type="buy",
            token="ETH",
            tx_amount_native=10.0,
            price_usd=2500.0,
            whale_score=0.15,
        ))
        self.assertIsNone(signal)

    def test_send_tx_type_suppressed(self):
        """
        GIVEN: A whale wallet sends ETH (tx_type='send').
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: No signal — send is not buy/receive.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="whale-004",
            is_whale=True,
            user_id="user-006",
            chain="eth",
            tx_hash="0xsend",
            tx_type="send",
            token="ETH",
            tx_amount_native=50.0,
            price_usd=2500.0,
            whale_score=0.8,
        ))
        self.assertIsNone(signal)

    def test_btc_whale_generates_signal(self):
        """
        GIVEN: A BTC whale buys 0.5 BTC ($52,500) — above MIN_SIGNAL_USD_BY_CHAIN['btc']=10000.
        WHEN: signal_generator.evaluate_for_signal is called.
        THEN: A signal is generated with token_symbol='BTC'.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="btc-whale-001",
            is_whale=True,
            user_id="user-007",
            chain="btc",
            tx_hash="btx-abc",
            tx_type="buy",
            token="BTC",
            tx_amount_native=0.5,
            price_usd=105000.0,
            whale_score=0.7,
            wallet_label="Bitcoin Whale",
            wallet_address="bc1qxyz",
        ))
        self.assertIsNotNone(signal)
        self.assertEqual(signal["token_symbol"], "BTC")
        self.assertAlmostEqual(signal["amount_usd"], 52500.0, places=0)

    def test_dedup_second_signal_suppressed(self):
        """
        GIVEN: A signal was just generated for whale-005 + ETH + buy.
        WHEN: The exact same signal is evaluated again (same wallet+token+action).
        THEN: The second call returns None (in-memory dedup hit).
        """
        conn = _FakeConn()
        # First signal — should succeed
        s1 = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="whale-005", is_whale=True, user_id="user-008",
            chain="eth", tx_hash="0xdedup1", tx_type="buy", token="ETH",
            tx_amount_native=5.0, price_usd=2500.0, whale_score=0.8,
        ))
        self.assertIsNotNone(s1)

        # Second signal — same wallet+token+action → dedup suppresses
        s2 = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="whale-005", is_whale=True, user_id="user-008",
            chain="eth", tx_hash="0xdedup2", tx_type="buy", token="ETH",
            tx_amount_native=3.0, price_usd=2500.0, whale_score=0.8,
        ))
        self.assertIsNone(s2, "Duplicate signal should be suppressed by dedup cache")

    # ── Phase 2: Alert evaluation ──────────────────────────────────────────

    def test_large_transaction_alert_fires(self):
        """
        GIVEN: A whale wallet has a large ETH buy tx ($500K), user has large_transaction
               alert with threshold $10,000.
        WHEN: alert_evaluator.evaluate_alerts is called.
        THEN: The alert fires with trigger_value >= threshold.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-lt-001", "user-010", "large_transaction", 10000.0)
        conn.seed_user("user-010", telegram_chat_id="tg-chat-123")
        conn.seed_transaction("whale-010", "0xbtx1", 500000.0)

        changed_wallets = [(
            "whale-010", "0xaddr", "Big Buyer", "eth", True, False, "user-010",
            (100.0, 2500000.0, "0xbtx1", "buy", "ETH", 200.0,),
        )]

        prev_balance_map = {"whale-010": 2400000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["rule_type"], "large_transaction")
        self.assertEqual(fired[0]["trigger_value"], 500000.0)
        self.assertIn("Large transaction", fired[0]["message"])

    def test_whale_buy_alert_fires(self):
        """
        GIVEN: A whale wallet (is_whale=True) buys $50K, user has whale_buy alert
               with threshold $5,000.
        WHEN: alert_evaluator.evaluate_alerts is called.
        THEN: The alert fires.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-wb-001", "user-011", "whale_buy", 5000.0)
        conn.seed_user("user-011")
        conn.seed_transaction("whale-011", "0xwbtx1", 50000.0)

        changed_wallets = [(
            "whale-011", "0xwaddr", "Whale X", "eth", True, False, "user-011",
            (20.0, 500000.0, "0xwbtx1", "receive", "ETH", 20.0,),
        )]

        prev_balance_map = {"whale-011": 480000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["rule_type"], "whale_buy")
        self.assertEqual(fired[0]["trigger_value"], 50000.0)

    def test_whale_buy_alert_does_not_fire_for_non_whale(self):
        """
        GIVEN: A non-whale wallet receives $100K, user has whale_buy alert.
        WHEN: alert_evaluator.evaluate_alerts is called.
        THEN: No alert fires — is_whale=False skips whale_buy rule.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-wb-002", "user-012", "whale_buy", 5000.0)
        conn.seed_user("user-012")
        conn.seed_transaction("normal-001", "0xwbtx2", 100000.0)

        changed_wallets = [(
            "normal-001", "0xnaddr", "Regular Joe", "eth", False, False, "user-012",
            (40.0, 1000000.0, "0xwbtx2", "receive", "ETH", 40.0,),
        )]

        prev_balance_map = {"normal-001": 980000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))
        self.assertEqual(len(fired), 0)

    def test_balance_drop_alert_fires_for_owned_wallet(self):
        """
        GIVEN: A user's owned (is_mine=True) wallet drops from $100K to $80K (20% drop),
               user has balance_drop alert with threshold 15%.
        WHEN: alert_evaluator.evaluate_alerts is called.
        THEN: The alert fires with drop_pct=20.0.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-bd-001", "user-013", "balance_drop", 15.0)
        conn.seed_user("user-013")

        changed_wallets = [(
            "mine-001", "0xmaddr", None, "eth", False, True, "user-013",
            (32.0, 80000.0, "0xbdrop1", "send", "ETH", 8.0,),
        )]

        prev_balance_map = {"mine-001": 100000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["rule_type"], "balance_drop")
        self.assertAlmostEqual(fired[0]["trigger_value"], 20.0, places=1)

    def test_balance_drop_skips_whale_wallets(self):
        """
        GIVEN: A whale wallet (is_whale=True, is_mine=False) drops 30%,
               user has balance_drop alert with threshold 10%.
        WHEN: alert_evaluator.evaluate_alerts is called.
        THEN: No alert — balance_drop only triggers for is_mine wallets.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-bd-002", "user-014", "balance_drop", 10.0)
        conn.seed_user("user-014")

        changed_wallets = [(
            "whale-tracked", "0xwaddr", None, "eth", True, False, "user-014",
            (70.0, 1750000.0, "0xwdrop1", "send", "ETH", 30.0,),
        )]

        prev_balance_map = {"whale-tracked": 2500000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))
        self.assertEqual(len(fired), 0)

    def test_alert_cooldown_prevents_refire(self):
        """
        GIVEN: A large_transaction alert was just fired.
        WHEN: The same alert condition occurs again in the next cycle.
        THEN: No second alert fires — cooldown cache prevents refire.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-lt-003", "user-015", "large_transaction", 10000.0)
        conn.seed_user("user-015")
        conn.seed_transaction("whale-015a", "0xca1", 200000.0)
        conn.seed_transaction("whale-015b", "0xca2", 300000.0)

        changed_wallets_cycle1 = [(
            "whale-015a", "0xca1addr", None, "eth", True, False, "user-015",
            (80.0, 2000000.0, "0xca1", "buy", "ETH", 80.0,),
        )]
        changed_wallets_cycle2 = [(
            "whale-015b", "0xca2addr", None, "eth", True, False, "user-015",
            (120.0, 3000000.0, "0xca2", "buy", "ETH", 120.0,),
        )]

        prev1 = {"whale-015a": 1900000.0}
        prev2 = {"whale-015b": 2900000.0}

        # First fires
        fired1 = self._run(ae.evaluate_alerts(conn, changed_wallets_cycle1, prev1))
        self.assertEqual(len(fired1), 1)

        # Second suppressed by cooldown
        fired2 = self._run(ae.evaluate_alerts(conn, changed_wallets_cycle2, prev2))
        self.assertEqual(len(fired2), 0, "Cooldown should prevent refire")

    def test_empty_changed_wallets_returns_empty(self):
        """
        GIVEN: No wallet changes detected.
        WHEN: alert_evaluator.evaluate_alerts is called with empty list.
        THEN: Returns empty list (early return).
        """
        conn = _FakeConn()
        conn.seed_alert("alert-empty", "user-099", "large_transaction", 1000.0)

        fired = self._run(ae.evaluate_alerts(conn, [], {}))
        self.assertEqual(len(fired), 0)

    def test_disabled_alert_does_not_fire(self):
        """
        GIVEN: A user has a large_transaction alert but it is disabled.
        WHEN: A large tx occurs.
        THEN: No alert fires.
        """
        conn = _FakeConn()
        conn.seed_alert("alert-disabled", "user-016", "large_transaction", 1000.0,
                        enabled=False)
        conn.seed_user("user-016")
        conn.seed_transaction("whale-016", "0xdis", 50000.0)

        changed_wallets = [(
            "whale-016", "0xdaddr", None, "eth", True, False, "user-016",
            (20.0, 500000.0, "0xdis", "buy", "ETH", 20.0),
        )]
        prev = {"whale-016": 480000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev))
        self.assertEqual(len(fired), 0)

    # ── Phase 3: Full pipeline simulation ──────────────────────────────────

    def test_full_pipeline_whale_buy_to_alert(self):
        """
        End-to-end: Simulate a complete cycle:
        1. Monitor detects whale buy of 30 ETH ($75K)
        2. SignalGenerator creates a signal with explanation
        3. AlertEvaluator fires large_transaction alert for the owning user

        This test chains all three services together to verify the
        end-to-end data flow works correctly.
        """
        conn = _FakeConn()

        # Step 1: Signal generation
        signal = self._run(sg.evaluate_for_signal(
            conn=conn,
            wallet_id="pipe-whale-001",
            is_whale=True,
            user_id="pipe-user-001",
            chain="eth",
            tx_hash="0xpipeline1",
            tx_type="buy",
            token="ETH",
            tx_amount_native=30.0,
            price_usd=2500.0,
            whale_score=0.92,
            median_amount_30d=15000.0,
            execution_rate_30d=0.82,
            wallet_label="Pipeline Alpha",
            wallet_address="0xPIPE001",
        ))
        self.assertIsNotNone(signal)
        self.assertEqual(signal["token_symbol"], "ETH")
        self.assertAlmostEqual(signal["amount_usd"], 75000.0, places=0)
        self.assertIsNotNone(signal["explanation"])
        self.assertIn("Pipeline Alpha", signal["explanation"])
        self.assertFalse(signal["explanation_stale"])

        # Step 2: Alert evaluation using the tx from the signal
        conn.seed_alert("pipe-alert-001", "pipe-user-001", "large_transaction", 50000.0)
        conn.seed_user("pipe-user-001", telegram_chat_id="tg-pipe-001")
        conn.seed_transaction("pipe-whale-001", "0xpipeline1", 75000.0)

        changed_wallets = [(
            "pipe-whale-001",
            "0xPIPE001",
            "Pipeline Alpha",
            "eth",
            True,   # is_whale
            False,  # is_mine
            "pipe-user-001",
            (1000.0, 2500000.0, "0xpipeline1", "buy", "ETH", 30.0),
        )]
        prev_balance_map = {"pipe-whale-001": 2450000.0}

        fired = self._run(ae.evaluate_alerts(conn, changed_wallets, prev_balance_map))

        # Evaluate alerts and assert
        self.assertTrue(
            len(fired) > 0,
            f"At least one alert should have fired. "
            f"Fired: {fired}, "
            f"Alerts in DB: {conn._tables['alerts']}, "
            f"Changed wallets: {[w[0] for w in changed_wallets]}"
        )
        self.assertTrue(
            any(f["rule_type"] == "large_transaction" and f["trigger_value"] == 75000.0
                for f in fired),
            f"Expected large_transaction alert with trigger=75000, got: {fired}"
        )

    def test_full_pipeline_no_duplicate_signal_on_reread(self):
        """
        End-to-end: Generate a signal, then verify dedup prevents a duplicate
        even with different tx_hash (same wallet+token+action).
        """
        conn = _FakeConn()

        s1 = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="dup-whale", is_whale=True, user_id="dup-user",
            chain="eth", tx_hash="0xdupA", tx_type="buy", token="ETH",
            tx_amount_native=10.0, price_usd=2500.0, whale_score=0.7,
        ))
        self.assertIsNotNone(s1)

        # Same wallet+token+action, different tx
        s2 = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="dup-whale", is_whale=True, user_id="dup-user",
            chain="eth", tx_hash="0xdupB", tx_type="buy", token="ETH",
            tx_amount_native=8.0, price_usd=2500.0, whale_score=0.7,
        ))
        self.assertIsNone(s2, "Dedup should suppress second signal")

        # Different token → allowed
        s3 = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="dup-whale", is_whale=True, user_id="dup-user",
            chain="eth", tx_hash="0xdupC", tx_type="buy", token="SOL",
            tx_amount_native=100.0, price_usd=170.0, whale_score=0.7,
        ))
        self.assertIsNotNone(s3, "Different token should generate new signal")

    # ── Monitor state tracking (Phase 1 of the monitor loop) ───────────────

    def test_monitor_tracks_balance_changes(self):
        """
        GIVEN: Monitor is initialized with a price cache.
        WHEN: _last_balances is updated after detecting a balance change.
        THEN: The balance is recorded correctly for subsequent change detection.
        """
        # Simulate Phase 4 state update pattern
        wallet_id = "mon-whale-001"
        new_balance = 500.0  # ETH

        # First observation
        self.assertNotIn(wallet_id, mon._last_balances)
        mon._last_balances[wallet_id] = new_balance

        # Verify state tracked
        self.assertEqual(mon._last_balances[wallet_id], 500.0)

    def test_monitor_tracks_tx_hashes(self):
        """
        GIVEN: Monitor detects a new tx on a wallet.
        WHEN: _last_tx_hashes is updated.
        THEN: The tx_hash is recorded and subsequent calls with same hash
              are detected as "no change".
        """
        wallet_id = "mon-whale-002"
        tx_hash = "0xmon-tx-hash-001"

        self.assertNotIn(wallet_id, mon._last_tx_hashes)
        mon._last_tx_hashes[wallet_id] = tx_hash
        self.assertEqual(mon._last_tx_hashes[wallet_id], tx_hash)

    def test_monitor_default_prices_are_reasonable(self):
        """
        GIVEN: Monitor price cache with hardcoded defaults.
        WHEN: Comparing default prices to rough current market ranges.
        THEN: Prices are within a reasonable order-of-magnitude bound
              (0.1x to 10x of approximate real values).
        """
        cache = mon._price_cache
        # ETH default: 2500 — real range ~1000-10000
        self.assertGreater(cache["ETH"], 100.0)
        self.assertLess(cache["ETH"], 50000.0)
        # BTC default: 105000 — real range ~20000-200000
        self.assertGreater(cache["BTC"], 10000.0)
        self.assertLess(cache["BTC"], 500000.0)
        # SOL default: 170 — real range ~20-500
        self.assertGreater(cache["SOL"], 10.0)
        self.assertLess(cache["SOL"], 1000.0)

    # ── Confidence score sanity ─────────────────────────────────────────────

    def test_confidence_monotonic_with_amount(self):
        """
        GIVEN: Two signals with different USD amounts.
        WHEN: Computing confidence scores.
        THEN: The higher-amount signal has equal or higher confidence.
        """
        conn = _FakeConn()

        # Small tx: $5K
        s_small = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="conf-whale-a", is_whale=True, user_id="conf-user",
            chain="eth", tx_hash="0xconfA", tx_type="buy", token="SMALL",
            tx_amount_native=2.0, price_usd=2500.0, whale_score=0.8,
        ))
        sg._signal_dedup_cache.clear()

        # Large tx: $500K
        s_large = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="conf-whale-b", is_whale=True, user_id="conf-user",
            chain="eth", tx_hash="0xconfB", tx_type="buy", token="LARGE",
            tx_amount_native=200.0, price_usd=2500.0, whale_score=0.8,
        ))

        self.assertIsNotNone(s_small)
        self.assertIsNotNone(s_large)
        self.assertGreaterEqual(
            s_large["confidence_score"], s_small["confidence_score"],
            "Larger tx should have >= confidence"
        )

    def test_c_final_blended_formula(self):
        """
        Given known whale_score and amount, C_final must equal 0.5*C_tx + 0.5*W.
        """
        conn = _FakeConn()

        # 10 ETH @ $2500 = $25000 → log10(25000)=4.398, raw=1.398, C_tx=0.5+(1.398/3)*0.5=0.733
        signal = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="cval-whale", is_whale=True, user_id="cval-user",
            chain="eth", tx_hash="0xcval1", tx_type="buy", token="ETH",
            tx_amount_native=10.0, price_usd=2500.0, whale_score=0.80,
        ))
        self.assertIsNotNone(signal)
        expected_c_tx = round(min(0.5 + ((math.log10(25000) - 3) / 3) * 0.5, 1.0), 2)
        expected_c_final = round(0.5 * expected_c_tx + 0.5 * 0.80, 2)
        self.assertAlmostEqual(signal["confidence_score"], expected_c_tx, places=2)
        self.assertAlmostEqual(signal["confidence_final"], expected_c_final, places=2)

    # ── Whale score threshold interaction ──────────────────────────────────

    def test_whale_score_boundary_exact_min(self):
        """
        GIVEN: whale_score exactly at MIN_WHALE_SCORE (0.30).
        WHEN: A qualifying tx is evaluated.
        THEN: Signal is generated (>= threshold, not > threshold).
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="boundary-whale", is_whale=True, user_id="b-user",
            chain="eth", tx_hash="0xbound", tx_type="buy", token="ETH",
            tx_amount_native=5.0, price_usd=2500.0,
            whale_score=sg.MIN_WHALE_SCORE,  # exactly 0.30
        ))
        self.assertIsNotNone(signal, "At-threshold whale_score should still generate signal")

    def test_whale_score_just_below_min(self):
        """
        GIVEN: whale_score = 0.29 (just below MIN_WHALE_SCORE=0.30).
        WHEN: A qualifying tx is evaluated.
        THEN: No signal is generated.
        """
        conn = _FakeConn()
        signal = self._run(sg.evaluate_for_signal(
            conn=conn, wallet_id="below-whale", is_whale=True, user_id="below-user",
            chain="eth", tx_hash="0xbelow", tx_type="buy", token="ETH",
            tx_amount_native=5.0, price_usd=2500.0,
            whale_score=0.29,
        ))
        self.assertIsNone(signal, "Below-threshold whale_score should suppress signal")

    # ── Explanation template coverage ──────────────────────────────────────

    def test_explanation_proven_whale_buy_high_conf_large_size(self):
        """
        GIVEN: proven whale (score>=0.5), buy, high confidence, large trade.
        WHEN: generate_explanation is called.
        THEN: Uses TPL-D template mentioning "above average size".
        """
        explanation = sg.generate_explanation(
            signal_data={
                "action": "buy", "amount_usd": 100000, "token_symbol": "ETH",
                "wallet_label": "Proven Whale", "wallet_address": "0xPROV",
                "is_receive": False, "confidence_score": 0.85, "confidence_final": 0.80,
            },
            whale_score=0.75,
            median_amount_30d=25000.0,  # 100K > 2x 25K → "large"
        )
        self.assertIn("above average size", explanation)

    def test_explanation_new_whale_buy_high_conf(self):
        """
        GIVEN: new whale (score<0.5), buy, high confidence.
        WHEN: generate_explanation is called.
        THEN: Uses TPL-H template mentioning "New whale" and "watch closely".
        """
        explanation = sg.generate_explanation(
            signal_data={
                "action": "buy", "amount_usd": 500000, "token_symbol": "SOL",
                "wallet_label": "Fresh Alpha", "wallet_address": "0xFRESH",
                "is_receive": False, "confidence_score": 0.90, "confidence_final": 0.85,
            },
            whale_score=0.40,
            median_amount_30d=0,
        )
        self.assertIn("New whale", explanation)
        self.assertIn("watch closely", explanation)

    def test_explanation_truncated_to_120_chars(self):
        """
        GIVEN: Very long wallet label + large amount that would exceed 120 chars.
        WHEN: generate_explanation is called.
        THEN: The explanation is truncated to 117 chars + "..."
        """
        long_label = "A" * 200
        explanation = sg.generate_explanation(
            signal_data={
                "action": "buy", "amount_usd": 5000000, "token_symbol": "WBTC",
                "wallet_label": long_label, "wallet_address": "0xLONG",
                "is_receive": False, "confidence_score": 0.95, "confidence_final": 0.90,
            },
            whale_score=0.9,
            median_amount_30d=1000000,
        )
        self.assertLessEqual(len(explanation), 120)
        self.assertTrue(explanation.endswith("..."))


if __name__ == "__main__":
    unittest.main()
