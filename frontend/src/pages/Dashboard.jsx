import React, { useState, useEffect, useCallback } from 'react';

import { API_BASE } from '../config';

async function apiFetch(path, token, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

function timeAgo(timestamp) {
  if (!timestamp) return '—';
  const seconds = Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function Dashboard({ token }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const d = await apiFetch('/dashboard', token);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading) return <div className="loading">Loading dashboard</div>;
  if (error) return <div className="card">Error: {error}</div>;
  if (!data) return null;

  const { portfolio, wallets, recent_transactions, alerts, copy_trade_signals } = data;

  return (
    <div>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '24px', color: '#8b5cf6' }}>
        ◈ Dashboard
      </h1>

      {/* Stats bar */}
      <div className="stats-bar">
        <div className="stat-card">
          <div className="stat-label">Portfolio Value</div>
          <div className="stat-value">${portfolio.total_value_usd.toLocaleString()}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Wallets Tracked</div>
          <div className="stat-value">{portfolio.wallets_tracked}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Whale Wallets</div>
          <div className="stat-value" style={{ color: '#8b5cf6' }}>{portfolio.whale_wallets}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Active Alerts</div>
          <div className="stat-value" style={{ color: '#f59e0b' }}>
            {alerts.filter(a => a.enabled).length}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        {/* Wallets */}
        <div className="card">
          <div className="card-title">◎ Top Wallets</div>
          {wallets.length === 0 ? (
            <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
              No wallets tracked yet. Add one in the Wallets tab.
            </p>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Chain</th>
                    <th>Label</th>
                    <th>Address</th>
                    <th>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {wallets.slice(0, 5).map(w => (
                    <tr key={w.id}>
                      <td>
                        <span className={`chain-dot ${w.chain}`} />
                        {w.chain.toUpperCase()}
                      </td>
                      <td>{w.label || '—'}</td>
                      <td className="address">{truncateAddress(w.address)}</td>
                      <td>${w.balance_usd > 0 ? w.balance_usd.toLocaleString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Copy Trade Signals */}
        <div className="card">
          <div className="card-title">◑ Copy Trade Signals</div>
          {copy_trade_signals.length === 0 ? (
            <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
              No signals yet. Whale buy activity will generate signals.
            </p>
          ) : (
            copy_trade_signals.map(s => (
              <div key={s.id} className="signal-card">
                <div className="signal-info">
                  <div className="signal-token">{s.token_symbol} {s.action.toUpperCase()}</div>
                  <div className="signal-meta">
                    {s.wallet_label} · ${s.amount_usd.toLocaleString()} · {timeAgo(s.created_at)}
                  </div>
                </div>
                <span className="signal-confidence" style={{
                  color: s.confidence_score > 0.7 ? '#10b981' : '#f59e0b'
                }}>
                  {(s.confidence_score * 100).toFixed(0)}%
                </span>
                <button className="btn btn-success btn-sm">Mirror</button>
                <button className="btn btn-secondary btn-sm">Analyze</button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Recent Activity */}
      <div className="card">
        <div className="card-title">◎ Recent Activity</div>
        {recent_transactions.length === 0 ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
            No transactions yet. Activity will appear here when wallets are tracked.
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Chain</th>
                  <th>Wallet</th>
                  <th>Amount</th>
                  <th>Value</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {recent_transactions.slice(0, 10).map(t => (
                  <tr key={t.id}>
                    <td>
                      <span className={`tx-badge ${t.type}`}>{t.type}</span>
                    </td>
                    <td>
                      <span className={`chain-dot ${t.chain}`} />
                      {t.chain.toUpperCase()}
                    </td>
                    <td>{t.wallet_label || truncateAddress(t.wallet_address)}</td>
                    <td>{t.amount} {t.token}</td>
                    <td>${t.usd_value.toLocaleString()}</td>
                    <td className="time-ago">{timeAgo(t.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Alert Config */}
      <div className="card">
        <div className="card-title">◐ Alert Configuration</div>
        {alerts.length === 0 ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
            No alerts configured. Set up alerts in the Alerts tab.
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Threshold</th>
                  <th>Enabled</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map(a => (
                  <tr key={a.id}>
                    <td>{a.rule_type}</td>
                    <td>${a.threshold.toLocaleString()}</td>
                    <td>
                      <label className="toggle">
                        <input type="checkbox" checked={a.enabled} readOnly />
                        <span className="toggle-slider" />
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export default Dashboard;
