"""
ChainWatch Health Metrics Service
==================================
Provides application-level metrics for monitoring and alerting.
Separate from the health check endpoint — this is for internal use
by the cron job and diagnostic endpoints.

Metrics exposed:
- request_count: total HTTP requests processed
- error_count: total 5xx errors
- endpoint_latency: per-endpoint latency tracking (ring buffer)
- db_query_count: total DB queries executed
- db_error_count: total DB query errors
- uptime_seconds: time since module load
"""
import time
import logging
from typing import Dict, Optional
from collections import defaultdict

logger = logging.getLogger("chainwatch.metrics")

# ── Ring buffer for per-endpoint latency ──────────────────────────────
_MAX_LATENCY_SAMPLES = 100

# Module-level state (reset on restart — this is intentional for in-memory metrics)
_metrics: Dict = {
    "request_count": 0,
    "error_count": 0,
    "db_query_count": 0,
    "db_error_count": 0,
    "start_time": time.time(),
    "endpoint_latency": defaultdict(list),  # method:path → [latency_s, ...]
}


def record_request(method: str, path: str, status_code: int, latency_s: float) -> None:
    """Record an HTTP request observation."""
    _metrics["request_count"] += 1
    if status_code >= 500:
        _metrics["error_count"] += 1

    key = f"{method.upper()} {path}"
    buf = _metrics["endpoint_latency"][key]
    buf.append(latency_s)
    # Trim to max size (ring buffer)
    while len(buf) > _MAX_LATENCY_SAMPLES:
        buf.pop(0)


def record_db_query(success: bool = True) -> None:
    """Record a DB query observation."""
    _metrics["db_query_count"] += 1
    if not success:
        _metrics["db_error_count"] += 1


def get_metrics() -> dict:
    """
    Return a snapshot of all collected metrics.
    Computes p50/p95/p99 latency per endpoint from the ring buffer.
    """
    uptime = time.time() - _metrics["start_time"]

    endpoint_stats = {}
    for key, samples in _metrics["endpoint_latency"].items():
        if not samples:
            continue
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        endpoint_stats[key] = {
            "count": n,
            "p50_ms": round(sorted_samples[int(n * 0.50)] * 1000, 1),
            "p95_ms": round(sorted_samples[int(n * 0.95)] * 1000, 1) if n >= 20 else None,
            "p99_ms": round(sorted_samples[int(n * 0.99)] * 1000, 1) if n >= 100 else None,
            "avg_ms": round(sum(sorted_samples) / n * 1000, 1),
        }

    return {
        "uptime_seconds": round(uptime, 1),
        "uptime_human": _format_duration(uptime),
        "request_count": _metrics["request_count"],
        "error_count": _metrics["error_count"],
        "error_rate": round(_metrics["error_count"] / max(_metrics["request_count"], 1) * 100, 2),
        "db_query_count": _metrics["db_query_count"],
        "db_error_count": _metrics["db_error_count"],
        "db_error_rate": round(_metrics["db_error_count"] / max(_metrics["db_query_count"], 1) * 100, 2),
        "endpoint_latency": endpoint_stats,
    }


def reset_metrics() -> None:
    """Reset all metrics (useful for testing)."""
    _metrics["request_count"] = 0
    _metrics["error_count"] = 0
    _metrics["db_query_count"] = 0
    _metrics["db_error_count"] = 0
    _metrics["start_time"] = time.time()
    _metrics["endpoint_latency"].clear()


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    elif seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    else:
        return f"{seconds / 86400:.1f}d"
