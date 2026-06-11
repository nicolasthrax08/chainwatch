"""
Tests for services/metrics_middleware.py
=========================================
FastAPI middleware that records request/response metrics.
Tests cover: health-path skipping, success recording, error recording,
exception propagation, and metrics-failure isolation.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from starlette.responses import Response
from starlette.requests import Request

from services.metrics_middleware import MetricsMiddleware


def _make_request(path="/api/wallets", method="GET"):
    """Create a minimal Starlette Request-like object for testing."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    receive = AsyncMock()
    request = Request(scope, receive)
    return request


def _make_response(status_code=200):
    """Create a minimal Response."""
    return Response(status_code=status_code)


class TestMetricsMiddlewareHealthPathSkip:
    """Health endpoints should be skipped to avoid self-referential noise."""

    @pytest.mark.asyncio
    async def test_health_path_skipped_no_record(self):
        """Requests to /api/health/* should not call record_request."""
        request = _make_request("/api/health")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            response = await middleware.dispatch(request, call_next)
            mock_record.assert_not_called()
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_nested_path_skipped(self):
        """Nested health paths like /api/health/detailed should also be skipped."""
        request = _make_request("/api/health/detailed")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_health_path_records(self):
        """Non-health paths should call record_request."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            mock_record.assert_called_once()


class TestMetricsMiddlewareSuccessRecording:
    """Successful requests should record method, path, status, latency."""

    @pytest.mark.asyncio
    async def test_records_200_response(self):
        request = _make_request("/api/wallets", "GET")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
            mock_record.assert_called_once()
            args = mock_record.call_args[0]
            assert args[0] == "GET"
            assert args[1] == "/api/wallets"
            assert args[2] == 200
            assert isinstance(args[3], float)
            assert args[3] >= 0

    @pytest.mark.asyncio
    async def test_records_post_method(self):
        request = _make_request("/api/wallets", "POST")
        call_next = AsyncMock(return_value=_make_response(201))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            args = mock_record.call_args[0]
            assert args[0] == "POST"
            assert args[2] == 201

    @pytest.mark.asyncio
    async def test_records_404_response(self):
        request = _make_request("/api/nonexistent")
        call_next = AsyncMock(return_value=_make_response(404))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            args = mock_record.call_args[0]
            assert args[2] == 404

    @pytest.mark.asyncio
    async def test_records_500_response(self):
        """Even 500 responses from call_next should be recorded (not exception)."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(return_value=_make_response(500))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            args = mock_record.call_args[0]
            assert args[2] == 500


class TestMetricsMiddlewareExceptionHandling:
    """Exceptions from call_next should be recorded as 500 and re-raised."""

    @pytest.mark.asyncio
    async def test_exception_recorded_as_500(self):
        request = _make_request("/api/wallets")
        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            with pytest.raises(RuntimeError, match="boom"):
                await middleware.dispatch(request, call_next)
            mock_record.assert_called_once()
            args = mock_record.call_args[0]
            assert args[0] == "GET"
            assert args[1] == "/api/wallets"
            assert args[2] == 500
            assert isinstance(args[3], float)

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        """The original exception must propagate to the caller."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(side_effect=ValueError("specific error"))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request"):
            with pytest.raises(ValueError, match="specific error"):
                await middleware.dispatch(request, call_next)

    @pytest.mark.asyncio
    async def test_exception_with_latency_positive(self):
        """Latency should be positive even when exception occurs."""
        request = _make_request("/api/wallets")

        async def fail_fast(request):
            raise RuntimeError("fast fail")

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            with pytest.raises(RuntimeError):
                await middleware.dispatch(request, fail_fast)
            args = mock_record.call_args[0]
            assert args[3] >= 0
            assert isinstance(args[3], float)


class TestMetricsMiddlewareMetricsFailureIsolation:
    """Metrics recording failures must never break the app."""

    @pytest.mark.asyncio
    async def test_record_request_raises_on_success(self):
        """If record_request raises, the response should still be returned."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request", side_effect=RuntimeError("metrics down")):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_record_request_raises_on_exception(self):
        """If record_request raises during exception handling, original exception propagates."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(side_effect=ValueError("original"))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request", side_effect=RuntimeError("metrics down")):
            with pytest.raises(ValueError, match="original"):
                await middleware.dispatch(request, call_next)


class TestMetricsMiddlewareLatency:
    """Latency should be measured and passed as a positive float."""

    @pytest.mark.asyncio
    async def test_latency_is_positive(self):
        request = _make_request("/api/wallets")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            latency = mock_record.call_args[0][3]
            assert latency >= 0

    @pytest.mark.asyncio
    async def test_latency_passed_as_float(self):
        """Latency should be a float type."""
        request = _make_request("/api/wallets")
        call_next = AsyncMock(return_value=_make_response(200))

        middleware = MetricsMiddleware(app=AsyncMock())
        with patch("services.metrics_middleware.record_request") as mock_record:
            await middleware.dispatch(request, call_next)
            latency = mock_record.call_args[0][3]
            assert isinstance(latency, float)


import asyncio  # needed for TestMetricsMiddlewareExceptionHandling test
