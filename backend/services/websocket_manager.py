"""
WebSocket connection manager.
Manages connected WebSocket clients with user_id scoping.
Provides per-user and broadcast push methods.
"""
import asyncio
import logging
from typing import Dict, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger("chainwatch.ws")

MAX_CONNECTIONS_PER_USER = 5


class WebSocketManager:
    """
    Thread-safe (async-safe) WebSocket connection registry.
    Scoped by user_id so each user only receives their own signals/alerts.
    """

    def __init__(self) -> None:
        # user_id → set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """Accept a new WebSocket connection and register it under user_id."""
        # Finding 14: Enforce per-user connection cap (max 5)
        # Fix: Accept the WS inside the lock to prevent TOCTOU race where
        # two concurrent connections both pass the cap check before either is added.
        async with self._lock:
            existing = self._connections.get(user_id, set())
            if len(existing) >= MAX_CONNECTIONS_PER_USER:
                logger.warning(
                    "WS connection cap reached for user=%s (max=%d), rejecting",
                    user_id, MAX_CONNECTIONS_PER_USER,
                )
                await websocket.close(code=4008, reason="Too many connections")
                return
            await websocket.accept()
            if user_id not in self._connections:
                self._connections[user_id] = set()
            self._connections[user_id].add(websocket)
        logger.info("WS connected: user=%s", user_id)

    async def disconnect(self, websocket: WebSocket, user_id: str) -> None:
        """Remove a WebSocket connection from the registry."""
        # Finding 13: Use .get() to avoid KeyError when cleaning dead connections
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns:
                conns.discard(websocket)
                if not conns:
                    self._connections.pop(user_id, None)
        logger.info("WS disconnected: user=%s", user_id)

    async def send_to_user(self, user_id: str, event: dict) -> int:
        """
        Send an event to all connections for a specific user.
        Returns the count of successful sends.
        Dead connections are cleaned up.
        """
        sent = 0
        async with self._lock:
            conns = self._connections.get(user_id, set()).copy()

        dead: Set[WebSocket] = set()
        for ws in conns:
            try:
                await ws.send_json(event)
                sent += 1
            except Exception:
                dead.add(ws)

        # Clean up dead connections
        if dead:
            async with self._lock:
                if user_id in self._connections:
                    self._connections[user_id] -= dead

        return sent

    async def broadcast(self, event: dict) -> int:
        """Broadcast an event to all connected users (system-wide events)."""
        sent = 0
        async with self._lock:
            all_users = list(self._connections.keys())
        for uid in all_users:
            sent += await self.send_to_user(uid, event)
        return sent

    async def close_all(self, code: int = 1001, reason: str = "Server shutting down") -> None:
        """Gracefully close all connected WebSockets (called on shutdown)."""
        async with self._lock:
            all_ws = [ws for conns in self._connections.values() for ws in conns]
            self._connections.clear()

        for ws in all_ws:
            try:
                await ws.close(code=code, reason=reason)
            except Exception as e:
                logger.debug("Error closing WebSocket during shutdown: %s", e)


# Module-level singleton
websocket_manager = WebSocketManager()
