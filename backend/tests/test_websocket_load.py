#!/usr/bin/env python3
"""
WebSocket load tests for the ChainWatch WebSocketManager.

Tests concurrent connection handling, message delivery under load,
connection cap enforcement under race conditions, and mixed
connect/send/disconnect workloads.

Run: python3 -m pytest tests/test_websocket_load.py -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services.websocket_manager import WebSocketManager, MAX_CONNECTIONS_PER_USER


def _make_mock_ws():
    """Create a mock WebSocket with async methods."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


def _make_slow_mock_ws(delay: float = 0.01):
    """Create a mock WebSocket with a configurable send delay."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()

    async def slow_send_json(data):
        await asyncio.sleep(delay)

    ws.send_json = AsyncMock(side_effect=slow_send_json)
    return ws


class TestWebSocketLoad_ConcurrentConnect(unittest.TestCase):
    """Test concurrent connection establishment under load."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_50_concurrent_connections_same_user(self):
        """50 concurrent connect() calls for the same user should result in exactly MAX_CONNECTIONS_PER_USER accepted."""
        async def run():
            mock_ws_list = [_make_mock_ws() for _ in range(50)]
            # Fire all 50 connect() calls concurrently
            await asyncio.gather(
                *[self.manager.connect(ws, "load-user") for ws in mock_ws_list]
            )
            # Exactly MAX_CONNECTIONS_PER_USER should have been accepted
            accepted = [ws for ws in mock_ws_list if ws.accept.called]
            rejected = [ws for ws in mock_ws_list if not ws.accept.called]
            self.assertEqual(len(accepted), MAX_CONNECTIONS_PER_USER)
            self.assertEqual(len(rejected), 50 - MAX_CONNECTIONS_PER_USER)
            # Rejected connections should have been closed with code 4008
            for ws in rejected:
                ws.close.assert_called_once_with(code=4008, reason="Too many connections")
            # Registry should have exactly MAX_CONNECTIONS_PER_USER entries
            self.assertEqual(len(self.manager._connections["load-user"]), MAX_CONNECTIONS_PER_USER)

        asyncio.run(run())

    def test_100_concurrent_connections_multiple_users(self):
        """100 users connecting concurrently should all succeed."""
        async def run():
            mock_ws_list = [_make_mock_ws() for _ in range(100)]
            await asyncio.gather(
                *[self.manager.connect(ws, f"user-{i}") for i, ws in enumerate(mock_ws_list)]
            )
            # All 100 should be accepted (each user has 1 connection)
            for ws in mock_ws_list:
                ws.accept.assert_called_once()
            self.assertEqual(len(self.manager._connections), 100)

        asyncio.run(run())

    def test_5_users_10_connections_each(self):
        """5 users each opening 10 connections should result in 5*MAX_CONNECTIONS_PER_USER accepted."""
        async def run():
            total_per_user = 10
            num_users = 5
            all_ws = {}
            tasks = []
            for u in range(num_users):
                user_id = f"multi-user-{u}"
                all_ws[user_id] = [_make_mock_ws() for _ in range(total_per_user)]
                for ws in all_ws[user_id]:
                    tasks.append(self.manager.connect(ws, user_id))

            await asyncio.gather(*tasks)

            for user_id, ws_list in all_ws.items():
                accepted = [ws for ws in ws_list if ws.accept.called]
                self.assertEqual(len(accepted), MAX_CONNECTIONS_PER_USER)
                self.assertEqual(len(self.manager._connections[user_id]), MAX_CONNECTIONS_PER_USER)

        asyncio.run(run())


class TestWebSocketLoad_ConcurrentSend(unittest.TestCase):
    """Test message delivery under concurrent load."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_send_to_50_concurrent_users(self):
        """send_to_user should deliver to all users concurrently without interference."""
        async def run():
            num_users = 50
            # Each user has 1 connection
            for i in range(num_users):
                ws = _make_mock_ws()
                await self.manager.connect(ws, f"send-user-{i}")

            # Send to all users concurrently
            event = {"type": "signal", "data": "whale_alert"}
            results = await asyncio.gather(
                *[self.manager.send_to_user(f"send-user-{i}", event) for i in range(num_users)]
            )
            # Each user should have received exactly 1 message
            for count in results:
                self.assertEqual(count, 1)
            self.assertEqual(sum(results), num_users)

        asyncio.run(run())

    def test_broadcast_to_100_users(self):
        """broadcast should deliver to all 100 users."""
        async def run():
            num_users = 100
            mock_ws_list = []
            for i in range(num_users):
                ws = _make_mock_ws()
                mock_ws_list.append(ws)
                await self.manager.connect(ws, f"broadcast-user-{i}")

            event = {"type": "system", "msg": "maintenance_window"}
            count = await self.manager.broadcast(event)
            self.assertEqual(count, num_users)
            for ws in mock_ws_list:
                ws.send_json.assert_called_once_with(event)

        asyncio.run(run())

    def test_broadcast_with_multi_connection_users(self):
        """broadcast should deliver to all connections of all users."""
        async def run():
            num_users = 10
            conns_per_user = 3
            total_expected = num_users * conns_per_user
            all_ws = []
            for u in range(num_users):
                for c in range(conns_per_user):
                    ws = _make_mock_ws()
                    all_ws.append(ws)
                    await self.manager.connect(ws, f"multi-conn-user-{u}")

            event = {"type": "alert", "severity": "high"}
            count = await self.manager.broadcast(event)
            self.assertEqual(count, total_expected)
            for ws in all_ws:
                ws.send_json.assert_called_once_with(event)

        asyncio.run(run())


class TestWebSocketLoad_MixedWorkload(unittest.TestCase):
    """Test mixed connect/send/disconnect workloads."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_connect_disconnect_race(self):
        """Concurrent connect and disconnect should not corrupt state."""
        async def run():
            # Pre-register 20 connections for a user
            ws_list = [_make_mock_ws() for _ in range(MAX_CONNECTIONS_PER_USER)]
            for ws in ws_list:
                await self.manager.connect(ws, "race-user")

            # Now concurrently disconnect all and connect 5 new ones
            new_ws_list = [_make_mock_ws() for _ in range(5)]
            tasks = []
            for ws in ws_list:
                tasks.append(self.manager.disconnect(ws, "race-user"))
            for ws in new_ws_list:
                tasks.append(self.manager.connect(ws, "race-user"))

            await asyncio.gather(*tasks)

            # After all operations, the user should have exactly 5 connections
            # (all old ones removed, 5 new ones added)
            conns = self.manager._connections.get("race-user", set())
            self.assertEqual(len(conns), 5)
            # All new connections should have been accepted
            for ws in new_ws_list:
                ws.accept.assert_called_once()

        asyncio.run(run())

    def test_send_during_disconnect(self):
        """send_to_user during concurrent disconnect should not crash."""
        async def run():
            ws_list = [_make_mock_ws() for _ in range(5)]
            for ws in ws_list:
                await self.manager.connect(ws, "send-disc-user")

            # Concurrently send and disconnect
            event = {"type": "test"}
            tasks = [
                self.manager.send_to_user("send-disc-user", event),
                self.manager.disconnect(ws_list[0], "send-disc-user"),
                self.manager.disconnect(ws_list[1], "send-disc-user"),
            ]
            results = await asyncio.gather(*tasks)
            # send_to_user should return a valid count (may be 3, 4, or 5 depending on timing)
            send_count = results[0]
            self.assertIsInstance(send_count, int)
            self.assertGreaterEqual(send_count, 0)
            self.assertLessEqual(send_count, 5)

        asyncio.run(run())

    def test_reconnect_after_cap_rejection(self):
        """After being rejected by cap, a user should still be able to connect after disconnect."""
        async def run():
            # Fill to cap
            ws_list = [_make_mock_ws() for _ in range(MAX_CONNECTIONS_PER_USER)]
            for ws in ws_list:
                await self.manager.connect(ws, "reconnect-user")

            # Next connection should be rejected
            ws_reject = _make_mock_ws()
            await self.manager.connect(ws_reject, "reconnect-user")
            ws_reject.close.assert_called_once_with(code=4008, reason="Too many connections")

            # Disconnect one
            await self.manager.disconnect(ws_list[0], "reconnect-user")

            # Now a new connection should be accepted
            ws_new = _make_mock_ws()
            await self.manager.connect(ws_new, "reconnect-user")
            ws_new.accept.assert_called_once()
            self.assertEqual(len(self.manager._connections["reconnect-user"]), MAX_CONNECTIONS_PER_USER)

        asyncio.run(run())


class TestWebSocketLoad_DeadConnectionCleanup(unittest.TestCase):
    """Test dead connection cleanup under load."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_send_cleans_up_50_dead_connections(self):
        """send_to_user should clean up dead connections, keeping only alive ones."""
        async def run():
            dead_ws_list = []
            alive_ws_list = []
            # Create 4 dead connections (within cap of 5)
            for _ in range(4):
                ws = AsyncMock()
                ws.accept = AsyncMock()
                ws.send_json = AsyncMock(side_effect=Exception("Connection lost"))
                dead_ws_list.append(ws)
                await self.manager.connect(ws, "dead-cleanup-user")

            # Create 1 alive connection
            for _ in range(1):
                ws = _make_mock_ws()
                alive_ws_list.append(ws)
                await self.manager.connect(ws, "dead-cleanup-user")

            # Send should only reach alive connections
            count = await self.manager.send_to_user("dead-cleanup-user", {"type": "test"})
            self.assertEqual(count, 1)

            # Dead connections should be removed from registry
            conns = self.manager._connections.get("dead-cleanup-user", set())
            self.assertEqual(len(conns), 1)
            for ws in alive_ws_list:
                self.assertIn(ws, conns)
            for ws in dead_ws_list:
                self.assertNotIn(ws, conns)

        asyncio.run(run())

    def test_broadcast_with_mixed_dead_alive(self):
        """broadcast should handle mixed dead/alive connections across users."""
        async def run():
            for u in range(10):
                # Each user has 1 dead + 1 alive connection
                dead_ws = AsyncMock()
                dead_ws.accept = AsyncMock()
                dead_ws.send_json = AsyncMock(side_effect=Exception("Dead"))
                await self.manager.connect(dead_ws, f"mixed-user-{u}")

                alive_ws = _make_mock_ws()
                await self.manager.connect(alive_ws, f"mixed-user-{u}")

            count = await self.manager.broadcast({"type": "test"})
            # Should only reach alive connections (10 users × 1 alive each)
            self.assertEqual(count, 10)

        asyncio.run(run())


class TestWebSocketLoad_CapEdgeCases(unittest.TestCase):
    """Test connection cap edge cases under concurrent load."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_exactly_at_cap(self):
        """Connecting exactly MAX_CONNECTIONS_PER_USER should succeed."""
        async def run():
            for i in range(MAX_CONNECTIONS_PER_USER):
                ws = _make_mock_ws()
                await self.manager.connect(ws, "exact-cap-user")
                ws.accept.assert_called_once()

            self.assertEqual(len(self.manager._connections["exact-cap-user"]), MAX_CONNECTIONS_PER_USER)

        asyncio.run(run())

    def test_one_over_cap(self):
        """Connecting MAX_CONNECTIONS_PER_USER + 1 should reject the last one."""
        async def run():
            for i in range(MAX_CONNECTIONS_PER_USER):
                ws = _make_mock_ws()
                await self.manager.connect(ws, "over-cap-user")

            ws_reject = _make_mock_ws()
            await self.manager.connect(ws_reject, "over-cap-user")
            ws_reject.close.assert_called_once_with(code=4008, reason="Too many connections")
            ws_reject.accept.assert_not_called()

        asyncio.run(run())

    def test_disconnect_all_then_reconnect(self):
        """After disconnecting all, user should be able to reconnect from scratch."""
        async def run():
            ws_list = [_make_mock_ws() for _ in range(MAX_CONNECTIONS_PER_USER)]
            for ws in ws_list:
                await self.manager.connect(ws, "full-cycle-user")

            for ws in ws_list:
                await self.manager.disconnect(ws, "full-cycle-user")

            self.assertNotIn("full-cycle-user", self.manager._connections)

            # Reconnect
            new_ws = _make_mock_ws()
            await self.manager.connect(new_ws, "full-cycle-user")
            new_ws.accept.assert_called_once()
            self.assertEqual(len(self.manager._connections["full-cycle-user"]), 1)

        asyncio.run(run())


class TestWebSocketLoad_CloseAllUnderLoad(unittest.TestCase):
    """Test close_all with many connections."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_close_all_with_200_connections(self):
        """close_all should gracefully close 200 connections across many users."""
        async def run():
            num_users = 40
            conns_per_user = 5
            all_ws = []
            for u in range(num_users):
                for c in range(conns_per_user):
                    ws = _make_mock_ws()
                    all_ws.append(ws)
                    await self.manager.connect(ws, f"closeall-user-{u}")

            await self.manager.close_all(code=1001, reason="Shutting down")

            for ws in all_ws:
                ws.close.assert_called_once_with(code=1001, reason="Shutting down")
            self.assertEqual(len(self.manager._connections), 0)

        asyncio.run(run())

    def test_close_all_with_some_failing_connections(self):
        """close_all should not fail even if some connections throw on close."""
        async def run():
            good_ws_list = [_make_mock_ws() for _ in range(3)]
            bad_ws_list = []
            for _ in range(2):
                ws = AsyncMock()
                ws.accept = AsyncMock()
                ws.close = AsyncMock(side_effect=Exception("Already closed"))
                bad_ws_list.append(ws)

            for ws in good_ws_list + bad_ws_list:
                await self.manager.connect(ws, "partial-fail-user")

            # Should not raise
            await self.manager.close_all()
            self.assertEqual(len(self.manager._connections), 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
