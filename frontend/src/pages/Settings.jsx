import React, { useState, useEffect } from 'react';
import { API_BASE } from '../config';

const THEME = {
  bg: '#13141a',
  cardBg: '#1c1d25',
  border: '#2a2b35',
  accent: '#8b5cf6',
  accentHover: '#7c3aed',
  muted: '#8b8f98',
  text: '#e4e5ea',
  success: '#10b8a6',
  danger: '#ef4444',
  dangerHover: '#dc2626',
  warning: '#f59e0b',
};

export default function Settings({ token }) {
  const [status, setStatus] = useState(null); // { connected, equity, account_id }
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // Fetch status on mount
  useEffect(() => {
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/user/alpaca/status`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
      } else {
        setStatus({ connected: false, equity: null, account_id: null });
      }
    } catch {
      setStatus({ connected: false, equity: null, account_id: null });
    } finally {
      setLoading(false);
    }
  };

  const handleConnect = async () => {
    setError('');
    setSuccessMsg('');
    if (!apiKey.trim() || !secretKey.trim()) {
      setError('Both API Key and Secret Key are required');
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/user/alpaca`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ api_key: apiKey.trim(), secret_key: secretKey.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`Connected! Equity: $${(data.equity || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
        setStatus({ connected: true, equity: data.equity, account_id: data.account_id });
        setApiKey('');
        setSecretKey('');
      } else {
        setError(data.detail || 'Connection failed');
      }
    } catch {
      setError('Network error — could not reach server');
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setError('');
    setSuccessMsg('');
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/user/alpaca`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setStatus({ connected: false, equity: null, account_id: null });
        setSuccessMsg('Alpaca account disconnected');
      } else {
        const data = await res.json();
        setError(data.detail || 'Disconnect failed');
      }
    } catch {
      setError('Network error — could not reach server');
    } finally {
      setSaving(false);
    }
  };

  const card = {
    backgroundColor: THEME.cardBg,
    border: `1px solid ${THEME.border}`,
    borderRadius: '12px',
    padding: '24px',
    maxWidth: '480px',
  };

  const inputStyle = {
    width: '100%',
    padding: '10px 14px',
    backgroundColor: THEME.bg,
    border: `1px solid ${THEME.border}`,
    borderRadius: '8px',
    color: THEME.text,
    fontSize: '0.875rem',
    fontFamily: 'monospace',
    outline: 'none',
    boxSizing: 'border-box',
  };

  const btnBase = {
    padding: '10px 20px',
    borderRadius: '8px',
    fontSize: '0.875rem',
    fontWeight: 600,
    cursor: saving ? 'not-allowed' : 'pointer',
    border: 'none',
    opacity: saving ? 0.6 : 1,
  };

  return (
    <div style={{ padding: '24px' }}>
      <h2 style={{
        fontSize: '1.5rem',
        fontWeight: 700,
        color: THEME.text,
        marginBottom: '24px',
      }}>
        Settings
      </h2>

      {/* Alpaca Connection Card */}
      <div style={card}>
        <h3 style={{
          fontSize: '1.1rem',
          fontWeight: 600,
          color: THEME.text,
          marginBottom: '6px',
        }}>
          Alpaca Paper Trading
        </h3>
        <p style={{
          fontSize: '0.8rem',
          color: THEME.muted,
          marginBottom: '20px',
          lineHeight: 1.5,
        }}>
          Connect your Alpaca paper trading account to execute mirror trades with your own isolated portfolio.
        </p>

        {/* Status indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          marginBottom: '20px',
          padding: '12px 16px',
          backgroundColor: THEME.bg,
          borderRadius: '8px',
          border: `1px solid ${THEME.border}`,
        }}>
          <span style={{
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            backgroundColor: loading ? THEME.warning : status?.connected ? THEME.success : THEME.danger,
            flexShrink: 0,
          }} />
          {loading ? (
            <span style={{ color: THEME.muted, fontSize: '0.85rem' }}>Checking connection...</span>
          ) : status?.connected ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              <span style={{ color: THEME.success, fontSize: '0.85rem', fontWeight: 600 }}>
                Connected
              </span>
              <span style={{ color: THEME.muted, fontSize: '0.75rem' }}>
                Account: {status.account_id?.slice(0, 8)}...{status.account_id?.slice(-4)} · Equity: ${status.equity?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          ) : (
            <span style={{ color: THEME.danger, fontSize: '0.85rem', fontWeight: 500 }}>
              Not connected
            </span>
          )}
        </div>

        {/* Error / Success messages */}
        {error && (
          <div style={{
            padding: '10px 14px',
            marginBottom: '16px',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            border: `1px solid rgba(239, 68, 68, 0.3)`,
            borderRadius: '8px',
            color: THEME.danger,
            fontSize: '0.85rem',
          }}>
            {error}
          </div>
        )}
        {successMsg && (
          <div style={{
            padding: '10px 14px',
            marginBottom: '16px',
            backgroundColor: 'rgba(16, 184, 166, 0.1)',
            border: `1px solid rgba(16, 184, 166, 0.3)`,
            borderRadius: '8px',
            color: THEME.success,
            fontSize: '0.85rem',
          }}>
            {successMsg}
          </div>
        )}

        {/* Connect form (shown when not connected) */}
        {!status?.connected && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <div>
              <label style={{
                display: 'block',
                fontSize: '0.8rem',
                color: THEME.muted,
                marginBottom: '6px',
                fontWeight: 500,
              }}>
                API Key ID
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="PK..."
                style={inputStyle}
                autoComplete="off"
              />
            </div>
            <div>
              <label style={{
                display: 'block',
                fontSize: '0.8rem',
                color: THEME.muted,
                marginBottom: '6px',
                fontWeight: 500,
              }}>
                Secret Key
              </label>
              <input
                type="password"
                value={secretKey}
                onChange={e => setSecretKey(e.target.value)}
                placeholder="Your Alpaca secret key"
                style={inputStyle}
                autoComplete="off"
              />
            </div>
            <button
              onClick={handleConnect}
              disabled={saving}
              style={{
                ...btnBase,
                backgroundColor: THEME.accent,
                color: '#fff',
                marginTop: '4px',
              }}
              onMouseEnter={e => { if (!saving) e.target.style.backgroundColor = THEME.accentHover; }}
              onMouseLeave={e => { e.target.style.backgroundColor = THEME.accent; }}
            >
              {saving ? 'Validating...' : 'Connect Alpaca'}
            </button>
          </div>
        )}

        {/* Disconnect button (shown when connected) */}
        {status?.connected && (
          <button
            onClick={handleDisconnect}
            disabled={saving}
            style={{
              ...btnBase,
              backgroundColor: 'transparent',
              color: THEME.danger,
              border: `1px solid ${THEME.danger}`,
              width: '100%',
            }}
            onMouseEnter={e => { if (!saving) { e.target.style.backgroundColor = 'rgba(239, 68, 68, 0.1)'; } }}
            onMouseLeave={e => { e.target.style.backgroundColor = 'transparent'; }}
          >
            {saving ? 'Disconnecting...' : 'Disconnect Alpaca'}
          </button>
        )}
      </div>
    </div>
  );
}
