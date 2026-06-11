#!/usr/bin/env python3
"""
Unit tests for the WebSocket connection manager.

Tests connect, disconnect, send_to_user, broadcast, close_all,
per-user connection cap, dead connection cleanup, and edge cases.

Run: python3 -m unittest tests/test_websocket_manager -v
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


class TestWebSocketManagerConnect(unittest.TestCase):
    """Test WebSocket connection registration."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_connect_accepts_and_registers(self):
        """connect() should accept the WS and register it under user_id."""
        async def run():
            ws = _make_mock_ws()
            await self.manager.connect(ws, "user-1")
            ws.accept.assert_called_once()
            self.assertIn("user-1", self.manager._connections)
            self.assertIn(ws, self.manager._connections["user-1"])

        asyncio.run(run())

    def test_connect_multiple_users(self):
        """Different users should have separate connection sets."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-2")
            self.assertIn(ws1, self.manager._connections["user-1"])
            self.assertIn(ws2, self.manager._connections["user-2"])
            self.assertNotIn(ws1, self.manager._connections["user-2"])

        asyncio.run(run())

    def test_connect_multiple_same_user(self):
        """Same user can have multiple connections."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-1")
            self.assertEqual(len(self.manager._connections["user-1"]), 2)

        asyncio.run(run())

    def test_connection_cap_rejects_excess(self):
        """Exceeding MAX_CONNECTIONS_PER_USER should reject with code 4008."""
        async def run():
            for i in range(MAX_CONNECTIONS_PER_USER):
                ws = _make_mock_ws()
                await self.manager.connect(ws, "user-cap")

            # Next connection should be rejected
            ws_reject = _make_mock_ws()
            await self.manager.connect(ws_reject, "user-cap")
            ws_reject.close.assert_called_once_with(code=4008, reason="Too many connections")
            ws_reject.accept.assert_not_called()

        asyncio.run(run())

    def test_connection_cap_constant(self):
        """MAX_CONNECTIONS_PER_USER should be 5."""
        self.assertEqual(MAX_CONNECTIONS_PER_USER, 5)


class TestWebSocketManagerDisconnect(unittest.TestCase):
    """Test WebSocket disconnection."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_disconnect_removes_connection(self):
        """disconnect() should remove the specific WS from the user's set."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-1")
            await self.manager.disconnect(ws1, "user-1")
            self.assertNotIn(ws1, self.manager._connections["user-1"])
            self.assertIn(ws2, self.manager._connections["user-1"])

        asyncio.run(run())

    def test_disconnect_last_removes_user_entry(self):
        """Disconnecting the last WS for a user should remove the user entry."""
        async def run():
            ws = _make_mock_ws()
            await self.manager.connect(ws, "user-1")
            await self.manager.disconnect(ws, "user-1")
            self.assertNotIn("user-1", self.manager._connections)

        asyncio.run(run())

    def test_disconnect_unknown_user_is_noop(self):
        """Disconnecting a user with no connections should not error."""
        async def run():
            ws = _make_mock_ws()
            await self.manager.disconnect(ws, "nonexistent-user")
            # Should not raise

        asyncio.run(run())

    def test_disconnect_unknown_ws_is_noop(self):
        """Disconnecting a WS that was never registered should not error."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.disconnect(ws2, "user-1")  # ws2 was never added
            self.assertIn(ws1, self.manager._connections["user-1"])

        asyncio.run(run())


class TestWebSocketManagerSendToUser(unittest.TestCase):
    """Test per-user message sending."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_send_to_user_delivers_to_all_connections(self):
        """send_to_user should send to all connections for the user."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-1")
            event = {"type": "signal", "data": "test"}
            count = await self.manager.send_to_user("user-1", event)
            self.assertEqual(count, 2)
            ws1.send_json.assert_called_once_with(event)
            ws2.send_json.assert_called_once_with(event)

        asyncio.run(run())

    def test_send_to_user_returns_zero_for_unknown(self):
        """send_to_user for unknown user should return 0."""
        async def run():
            count = await self.manager.send_to_user("nonexistent", {"type": "test"})
            self.assertEqual(count, 0)

        asyncio.run(run())

    def test_send_cleans_up_dead_connections(self):
        """Dead connections should be cleaned up after send failure."""
        async def run():
            ws_dead = AsyncMock()
            ws_dead.accept = AsyncMock()
            ws_dead.send_json = AsyncMock(side_effect=Exception("Connection lost"))
            ws_alive = _make_mock_ws()
            await self.manager.connect(ws_dead, "user-1")
            await self.manager.connect(ws_alive, "user-1")
            count = await self.manager.send_to_user("user-1", {"type": "test"})
            self.assertEqual(count, 1)
            # Dead connection should be removed
            self.assertNotIn(ws_dead, self.manager._connections.get("user-1", set()))

        asyncio.run(run())


class TestWebSocketManagerBroadcast(unittest.TestCase):
    """Test broadcast to all users."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_broadcast_sends_to_all_users(self):
        """broadcast should send to all connected users."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-2")
            event = {"type": "system", "msg": "maintenance"}
            count = await self.manager.broadcast(event)
            self.assertEqual(count, 2)
            ws1.send_json.assert_called_once_with(event)
            ws2.send_json.assert_called_once_with(event)

        asyncio.run(run())

    def test_broadcast_empty_returns_zero(self):
        """broadcast with no connections should return 0."""
        async def run():
            count = await self.manager.broadcast({"type": "test"})
            self.assertEqual(count, 0)

        asyncio.run(run())


class TestWebSocketManagerCloseAll(unittest.TestCase):
    """Test graceful shutdown."""

    def setUp(self):
        self.manager = WebSocketManager()

    def test_close_all_closes_everything(self):
        """close_all should close all connections and clear the registry."""
        async def run():
            ws1 = _make_mock_ws()
            ws2 = _make_mock_ws()
            await self.manager.connect(ws1, "user-1")
            await self.manager.connect(ws2, "user-2")
            await self.manager.close_all(code=1001, reason="Shutting down")
            ws1.close.assert_called_once_with(code=1001, reason="Shutting down")
            ws2.close.assert_called_once_with(code=1001, reason="Shutting down")
            self.assertEqual(len(self.manager._connections), 0)

        asyncio.run(run())

    def test_close_all_empty_is_noop(self):
        """close_all with no connections should not error."""
        async def run():
            await self.manager.close_all()
            # Should not raise

        asyncio.run(run())

    def test_close_all_swallows_errors(self):
        """close_all should not raise even if individual closes fail."""
        async def run():
            ws_bad = AsyncMock()
            ws_bad.accept = AsyncMock()
            ws_bad.close = AsyncMock(side_effect=Exception("Already closed"))
            await self.manager.connect(ws_bad, "user-1")
            # Should not raise
            await self.manager.close_all()

        asyncio.run(run())


class TestWebSocketManagerSingleton(unittest.TestCase):
    """Test the module-level singleton."""

    def test_singleton_exists(self):
        """The module should export a websocket_manager singleton."""
        from services.websocket_manager import websocket_manager
        self.assertIsInstance(websocket_manager, WebSocketManager)


if __name__ == "__main__":
    unittest.main(verbosity=2)
