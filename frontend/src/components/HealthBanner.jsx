import React, { useState, useEffect, useCallback } from 'react';
import { checkApiHealth } from '../api';

/**
 * HealthBanner — polls /api/health and shows a non-intrusive banner
 * when the backend is degraded (DB down, 503, etc.).
 *
 * - Polls every 30 seconds
 * - Dismissible by clicking the X
 * - Auto-reappears if health degrades again after dismissal
 * - Only renders when there's an issue (zero noise when healthy)
 */
export function HealthBanner() {
  const [health, setHealth] = useState(null); // null = unknown, { ok, status, dbOk }
  const [dismissed, setDismissed] = useState(false);
  const [lastCheck, setLastCheck] = useState(null);

  const pollHealth = useCallback(async () => {
    try {
      const result = await checkApiHealth();
      setHealth(result);
      setLastCheck(Date.now());
      // Reset dismissed state if health recovers then degrades again
      if (!result.ok) setDismissed(false);
    } catch {
      setHealth({ ok: false, status: 'unreachable', dbOk: false });
      setLastCheck(Date.now());
    }
  }, []);

  useEffect(() => {
    // Initial check
    pollHealth();
    // Poll every 30 seconds
    const interval = setInterval(pollHealth, 30000);
    return () => clearInterval(interval);
  }, [pollHealth]);

  // Don't render if healthy, unknown, or user dismissed this incident
  if (!health || health.ok || dismissed) return null;

  const isDbDown = !health.dbOk;
  const isUnreachable = health.status === 'unreachable';

  let message;
  if (isUnreachable) {
    message = '⚠️ ChainWatch API is unreachable — the service may be restarting. Retrying automatically…';
  } else if (isDbDown) {
    message = '⚠️ Database temporarily unavailable — some features may be limited. Retrying automatically…';
  } else {
    message = '⚠️ Service degraded — some features may be limited. Retrying automatically…';
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '8px 16px',
        background: 'linear-gradient(135deg, #92400e, #b45309)',
        color: '#fef3c7',
        fontSize: '0.82rem',
        fontWeight: 500,
        gap: '12px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
        animation: 'healthBannerSlideIn 0.3s ease-out',
      }}
    >
      <span style={{ flex: 1, textAlign: 'center' }}>{message}</span>
      <button
        onClick={() => setDismissed(true)}
        style={{
          background: 'rgba(255,255,255,0.15)',
          border: '1px solid rgba(255,255,255,0.25)',
          borderRadius: '4px',
          color: '#fef3c7',
          cursor: 'pointer',
          fontSize: '0.75rem',
          padding: '2px 8px',
          lineHeight: 1.4,
          flexShrink: 0,
        }}
        title="Dismiss (will reappear if issue persists)"
      >
        Dismiss
      </button>
      <style>{`
        @keyframes healthBannerSlideIn {
          from { transform: translateY(-100%); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
