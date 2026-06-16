import React, { useState, useEffect } from 'react';
import { apiFetch } from '../api';
import { THEME } from '../theme';

export default function Settings({ token }) {
  const [status, setStatus] = useState(null); // { connected, equity, account_id }
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // ── Telegram state ─────────────────────────────────────────────────
  const [telegramChatId, setTelegramChatId] = useState('');
  const [telegramConfigured, setTelegramConfigured] = useState(false);
  const [telegramSaving, setTelegramSaving] = useState(false);
  const [telegramError, setTelegramError] = useState('');
  const [telegramSuccess, setTelegramSuccess] = useState('');

  // Fetch status on mount
  useEffect(() => {
    fetchStatus();
    fetchTelegramStatus();
  }, []);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const data = await apiFetch('/user/alpaca/status', token);
      setStatus(data);
    } catch {
      setStatus({ connected: false, equity: null, account_id: null });
    } finally {
      setLoading(false);
    }
  };

  // ── Telegram handlers ───────────────────────────────────────────────
  const fetchTelegramStatus = async () => {
    try {
      const data = await apiFetch('/user/telegram/status', token);
      setTelegramConfigured(data.telegram_configured || false);
      if (data.telegram_chat_id) {
        setTelegramChatId(data.telegram_chat_id);
      }
    } catch {
      // Endpoint may not exist yet; silently skip
      setTelegramConfigured(false);
    }
  };

  const handleTelegramSave = async () => {
    setTelegramError('');
    setTelegramSuccess('');
    if (!telegramChatId.trim()) {
      setTelegramError('Telegram Chat ID is required');
      return;
    }
    setTelegramSaving(true);
    try {
      await apiFetch('/user/telegram', token, {
        method: 'POST',
        body: JSON.stringify({ chat_id: telegramChatId.trim() }),
      });
      setTelegramConfigured(true);
      setTelegramSuccess('Telegram notifications enabled! You will receive alerts here.');
    } catch (e) {
      setTelegramError(e.message || 'Failed to save Telegram chat ID');
    } finally {
      setTelegramSaving(false);
    }
  };

  const handleTelegramDisconnect = async () => {
    setTelegramError('');
    setTelegramSuccess('');
    setTelegramSaving(true);
    try {
      await apiFetch('/user/telegram', token, { method: 'DELETE' });
      setTelegramConfigured(false);
      setTelegramChatId('');
      setTelegramSuccess('Telegram notifications disabled');
    } catch (e) {
      setTelegramError(e.message || 'Failed to disconnect Telegram');
    } finally {
      setTelegramSaving(false);
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
      const data = await apiFetch('/user/alpaca', token, {
        method: 'POST',
        body: JSON.stringify({ api_key: apiKey.trim(), secret_key: secretKey.trim() }),
      });
      setSuccessMsg(`Connected! Equity: $${(data.equity || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
      setStatus({ connected: true, equity: data.equity, account_id: data.account_id });
      setApiKey('');
      setSecretKey('');
    } catch (e) {
      setError(e.message || 'Connection failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setError('');
    setSuccessMsg('');
    setSaving(true);
    try {
      await apiFetch('/user/alpaca', token, { method: 'DELETE' });
      setStatus({ connected: false, equity: null, account_id: null });
      setSuccessMsg('Alpaca account disconnected');
    } catch (e) {
      setError(e.message || 'Disconnect failed');
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

      {/* Telegram Notification Card */}
      <div style={{ ...card, marginTop: '24px' }}>
        <h3 style={{
          fontSize: '1.1rem',
          fontWeight: 600,
          color: THEME.text,
          marginBottom: '6px',
        }}>
          📱 Telegram Notifications
        </h3>
        <p style={{
          fontSize: '0.8rem',
          color: THEME.muted,
          marginBottom: '20px',
          lineHeight: 1.5,
        }}>
          Receive whale alerts and signal notifications directly in your Telegram.
          Get your Chat ID from @userinfobot on Telegram.
        </p>

        {/* Telegram status indicator */}
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
            backgroundColor: telegramConfigured ? THEME.success : THEME.muted,
            flexShrink: 0,
          }} />
          <span style={{ color: telegramConfigured ? THEME.success : THEME.muted, fontSize: '0.85rem', fontWeight: 500 }}>
            {telegramConfigured ? `Connected (Chat ID: ${telegramChatId})` : 'Not configured'}
          </span>
        </div>

        {/* Telegram error / success */}
        {telegramError && (
          <div style={{
            padding: '10px 14px',
            marginBottom: '16px',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '8px',
            color: THEME.danger,
            fontSize: '0.85rem',
          }}>
            {telegramError}
          </div>
        )}
        {telegramSuccess && (
          <div style={{
            padding: '10px 14px',
            marginBottom: '16px',
            backgroundColor: 'rgba(16, 184, 166, 0.1)',
            border: '1px solid rgba(16, 184, 166, 0.3)',
            borderRadius: '8px',
            color: THEME.success,
            fontSize: '0.85rem',
          }}>
            {telegramSuccess}
          </div>
        )}

        {/* Telegram connect form */}
        {!telegramConfigured && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <div>
              <label style={{
                display: 'block',
                fontSize: '0.8rem',
                color: THEME.muted,
                marginBottom: '6px',
                fontWeight: 500,
              }}>
                Telegram Chat ID
              </label>
              <input
                type="text"
                value={telegramChatId}
                onChange={e => setTelegramChatId(e.target.value)}
                placeholder="e.g. 123456789"
                style={inputStyle}
                autoComplete="off"
              />
            </div>
            <button
              onClick={handleTelegramSave}
              disabled={telegramSaving}
              style={{
                ...btnBase,
                backgroundColor: THEME.accent,
                color: '#fff',
                marginTop: '4px',
              }}
              onMouseEnter={e => { if (!telegramSaving) e.target.style.backgroundColor = THEME.accentHover; }}
              onMouseLeave={e => { e.target.style.backgroundColor = THEME.accent; }}
            >
              {telegramSaving ? 'Saving...' : 'Enable Telegram'}
            </button>
          </div>
        )}

        {/* Telegram disconnect button */}
        {telegramConfigured && (
          <button
            onClick={handleTelegramDisconnect}
            disabled={telegramSaving}
            style={{
              ...btnBase,
              backgroundColor: 'transparent',
              color: THEME.danger,
              border: `1px solid ${THEME.danger}`,
              width: '100%',
            }}
            onMouseEnter={e => { if (!telegramSaving) { e.target.style.backgroundColor = 'rgba(239, 68, 68, 0.1)'; } }}
            onMouseLeave={e => { e.target.style.backgroundColor = 'transparent'; }}
          >
            {telegramSaving ? 'Disconnecting...' : 'Disable Telegram Notifications'}
          </button>
        )}
      </div>
    </div>
  );
}
