"""
ChainWatch Metrics Middleware
==============================
FastAPI middleware that records request/response metrics for the health_metrics service.
Mounts timing, status code, and endpoint info for every request.

Usage:
    from services.metrics_middleware import MetricsMiddleware
    app.add_middleware(MetricsMiddleware)
"""
import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from services.health_metrics import record_request

logger = logging.getLogger("chainwatch.metrics_middleware")


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Records request count, error count, and per-endpoint latency.
    Skips /api/health/* paths to avoid self-referential noise in metrics.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip health endpoints to avoid self-referential noise
        if request.url.path.startswith("/api/health"):
            return await call_next(request)

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as e:
            # Record the error and re-raise
            latency = time.monotonic() - start
            try:
                record_request(request.method, request.url.path, 500, latency)
            except Exception:
                pass  # Never let metrics break the app
            raise

        latency = time.monotonic() - start
        try:
            record_request(request.method, request.url.path, response.status_code, latency)
        except Exception:
            pass  # Never let metrics break the app

        return response
