/**
 * ChainWatch Shared API & Formatting Utilities
 * =============================================
 *
 * Central module for all API calls and string/formatting helpers
 * that were previously duplicated across every page component.
 *
 * Using this module ensures:
 * - Consistent error handling (no silent console.error-only failures)
 * - Consistent address truncation format
 * - Consistent currency formatting
 * - Single place to add request logging, retry logic, or auth refresh
 */
import { API_BASE } from './config';

/**
 * Make an authenticated API request.
 *
 * @param {string} path  — API path (e.g. '/signals/history')
 * @param {string} token — JWT bearer token
 * @param {object} [options] — extra fetch options (method, body, etc.)
 * @returns {Promise<any>} parsed JSON response
 * @throws {Error} on non-2xx status or network failure
 */
export async function apiFetch(path, token, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
  if (!res.ok) {
    // Try to extract server error message from response body
    let detail = `API error: ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* body not JSON — use status text */
    }
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Format a timestamp as a human-readable relative time string.
 * Handles future timestamps (clock skew) by clamping to 'just now'.
 *
 * @param {string|number|Date} timestamp
 * @returns {string} e.g. '5s ago', '3m ago', '2h ago', '7d ago', '—'
 */
export function timeAgo(timestamp) {
  if (!timestamp) return '—';
  const diffMs = Date.now() - new Date(timestamp).getTime();
  if (diffMs <= 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * Truncate a wallet address for display.
 *
 * @param {string} addr
 * @returns {string} e.g. '0xabcd...1234' or '—'
 */
export function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

/**
 * Format a numeric value in the selected currency.
 *
 * @param {number|null|undefined} value
 * @param {string} [currency='USD'] — 'USD' | 'BTC' | 'HKD'
 * @returns {string} e.g. '$1,234', '₿0.00001234', 'HK$9,600', '—'
 */
export function fmtTotal(value, currency = 'USD') {
  if (value == null) return '—';
  if (currency === 'BTC') return `₿${value.toLocaleString(undefined, { maximumFractionDigits: 8 })}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

/**
 * Format a wallet balance in the selected currency.
 * Reads the backend-converted field (e.g. balance_hkd, balance_btc).
 *
 * @param {object} wallet  — wallet object from the API
 * @param {string} [currency='USD']
 * @returns {string}
 */
export function fmtBalance(wallet, currency = 'USD') {
  if (!wallet) return '—';
  let value;
  if (currency === 'HKD') value = wallet.balance_hkd;
  else if (currency === 'BTC') value = wallet.balance_btc;
  else value = wallet.balance_usd;
  if (value == null) return '—'; // null/undefined → no data; 0 is valid
  if (currency === 'BTC') return `₿${value.toFixed(8)}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

/**
 * Format a duration in seconds to a human-readable string.
 *
 * @param {number|null|undefined} seconds
 * @returns {string} e.g. '45s', '3m 12s', '2h 15m', '1d 4h', '—'
 */
export function fmtDuration(seconds) {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/**
 * Signal status color mapping.
 * Used by CopyTrades, Dashboard, and any component that renders signal status.
 */
export const STATUS_COLORS = {
  pending: '#f59e0b',
  executed: '#10b981',
  failed: '#ef4444',
  stale: '#6b7280',
};

/**
 * Check backend API health without authentication.
 * Calls /api/health and returns a simplified health status.
 *
 * @returns {Promise<{ok: boolean, status: string, dbOk: boolean}>}
 *   - ok: true if the service is fully healthy
 *   - status: 'healthy' | 'degraded' | 'unreachable'
 *   - dbOk: true if the database is connected
 *
 * Never throws — returns { ok: false, status: 'unreachable', dbOk: false }
 * on any error (network failure, timeout, etc.).
 */
export async function checkApiHealth() {
  try {
    const res = await fetch(`${typeof API_BASE !== 'undefined' ? API_BASE : '/api'}/health`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      // Short timeout — we want fast feedback, not a long wait
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      // Got a response but it's an error status (e.g., 503)
      let body = {};
      try { body = await res.json(); } catch { /* ignore */ }
      return {
        ok: false,
        status: body.status || 'degraded',
        dbOk: body.db?.ok ?? false,
      };
    }
    const body = await res.json();
    return {
      ok: body.status === 'healthy',
      status: body.status || 'healthy',
      dbOk: body.db?.ok ?? false,
    };
  } catch {
    // Network error, timeout, DNS failure, etc.
    return { ok: false, status: 'unreachable', dbOk: false };
  }
}
