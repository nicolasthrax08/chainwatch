import React, { useState, useEffect, useCallback, useMemo } from 'react';

import { API_BASE } from '../config';
import { ChainBadge } from '../App';

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
  const diffMs = Date.now() - new Date(timestamp).getTime();
  // Clamp negative (future timestamps from clock skew) to 0
  if (diffMs <= 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

/**
 * Check if a wallet balance was updated recently (within last 2 minutes).
 * Used to show a "live" indicator on the dashboard.
 */
function isLiveUpdate(timestamp) {
  if (!timestamp) return false;
  const diffMs = Date.now() - new Date(timestamp).getTime();
  return diffMs >= 0 && diffMs < 120000; // within 2 minutes
}

/**
 * Format a balance in the selected currency.
 */
function fmtBalance(wallet, currency) {
  let value;
  if (currency === 'HKD') value = wallet.balance_hkd;
  else if (currency === 'BTC') value = wallet.balance_btc;
  else value = wallet.balance_usd;

  if (value == null) return '—';  // Finding: !value treated real 0 as no-data

  if (currency === 'BTC') return `₿${value.toFixed(8)}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

function fmtTotal(total, currency) {
  if (!total && total !== 0) return '—';
  // F5 fix: backend already provides converted totals; render them directly
  if (currency === 'BTC') return `₿${total.toLocaleString(undefined, { maximumFractionDigits: 8 })}`;
  if (currency === 'HKD') return `HK$${total.toLocaleString()}`;
  return `$${total.toLocaleString()}`;
}

/**
 * RiskBadge — renders a stylized badge for wallet risk profiling.
 * - Whale: purple "🐋 WHALE" badge
 */
function RiskBadge({ wallet }) {
  if (!wallet) return null;

  if (wallet.is_whale) {
    return (
      <span
        className="risk-badge whale"
        title="High-balance whale wallet"
        style={{
          display: 'inline-block',
          padding: '2px 8px',
          borderRadius: '12px',
          fontSize: '0.65rem',
          fontWeight: 700,
          letterSpacing: '0.5px',
          textTransform: 'uppercase',
          background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
          color: '#fff',
          marginLeft: '6px',
          verticalAlign: 'middle',
          boxShadow: '0 0 8px rgba(139, 92, 246, 0.5)',
        }}
      >
        🐋 Whale
      </span>
    );
  }

  return null;
}

const DASHBOARD_STATUS_COLORS = {
  pending: '#f59e0b',
  executed: '#10b981',
  failed: '#ef4444',
};

function DashboardAnalyzeModal({ signal, currency, onMirror, onClose }) {
  if (!signal) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#13141a',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: '12px',
          padding: '20px 24px',
          width: '340px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>{signal.token_symbol}</span>
            <span style={{
              fontSize: '10px', fontWeight: 600, padding: '2px 7px', borderRadius: '4px',
              background: signal.action === 'buy' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
              color: signal.action === 'buy' ? '#10b981' : '#ef4444',
            }}>{signal.action.toUpperCase()}</span>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#8b8f98', cursor: 'pointer', fontSize: '18px', lineHeight: 1, padding: '2px' }}
          >×</button>
        </div>

        {[
          ['Whale', signal.wallet_label],
          ['Address', signal.wallet_address ? `${signal.wallet_address.slice(0, 6)}...${signal.wallet_address.slice(-4)}` : '—'],
          ['Amount', fmtTotal(signal.amount_usd, currency)],
          ['Confidence', `${(signal.confidence_score * 100).toFixed(0)}%`],
          ['Final Confidence', `${(signal.confidence_final * 100).toFixed(0)}%`],
          ['Whale Score (at signal)', signal.score_at_generation?.toFixed(2) ?? '—'],
          ['Status', signal.status],
          ['Detected', timeAgo(signal.created_at)],
        ].map(([label, value]) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.05)', fontSize: '13px' }}>
            <span style={{ color: '#8b8f98' }}>{label}</span>
            <span style={{
              color: label === 'Confidence'
                ? (signal.confidence_score > 0.7 ? '#10b981' : '#f59e0b')
                : label === 'Final Confidence'
                ? (signal.confidence_final > 0.7 ? '#10b981' : '#f59e0b')
                : label === 'Status'
                ? (DASHBOARD_STATUS_COLORS[signal.status] || '#8b8f98')
                : '#e2e8f0',
              fontWeight: (label === 'Confidence' || label === 'Final Confidence') ? 600 : 400,
            }}>{value}</span>
          </div>
        ))}

        <div style={{ display: 'flex', gap: '8px', marginTop: '16px' }}>
          {signal.status !== 'executed' && (
            <button
              className="btn btn-success btn-sm"
              style={{ flex: 1, padding: '8px 0', fontSize: '13px' }}
              onClick={() => { onClose(); onMirror(signal.id, signal.token_symbol, signal.amount_usd); }}
            >
              Mirror trade
            </button>
          )}
          <button
            className="btn btn-secondary btn-sm"
            style={{ padding: '8px 16px', fontSize: '13px' }}
            onClick={onClose}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}

function Dashboard({ token, currency }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sentiment, setSentiment] = useState(null);
  const [mirroringIds, setMirroringIds] = useState(new Set());
  const [analyzeSignal, setAnalyzeSignal] = useState(null);

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        apiFetch('/dashboard', token),
        apiFetch('/whale-sentiment', token).catch(() => null),
      ]);
      setData(d);
      setSentiment(s);
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

  // Derive displayed totals — uses backend-filtered personal wallet total
  const displayTotal = useMemo(() => {
    if (!data) return 0;
    if (currency === 'HKD') return data.portfolio.total_value_hkd;
    if (currency === 'BTC') return data.portfolio.total_value_btc;
    return data.portfolio.total_value_usd;
  }, [data, currency]);

  if (loading) return <div className="loading">Loading dashboard</div>;
  if (error) return <div className="card">Error: {error}</div>;
  if (!data) return null;

  const { portfolio, personal_wallets = [], whale_wallets_list = [], recent_transactions = [], alerts = [], copy_trade_signals = [] } = data;

  return (
    <div>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '24px', color: '#8b5cf6' }}>
        ◈ Dashboard
      </h1>

      {/* Whale Sentiment Meter */}
      <div
        className="card"
        style={{
          marginBottom: '20px',
          padding: '16px 20px',
          background: 'linear-gradient(135deg, rgba(124,58,237,0.08), rgba(139,92,246,0.04))',
          border: '1px solid rgba(139,92,246,0.2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '1.1rem' }}>🐋</span>
            <span style={{ fontWeight: 700, fontSize: '0.85rem', color: '#c4b5fd', letterSpacing: '0.5px' }}>
              WHALE SENTIMENT
            </span>
          </div>
          {sentiment && (
            <span
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                padding: '3px 10px',
                borderRadius: '8px',
                background:
                  sentiment.classification.includes('Bullish')
                    ? 'rgba(16,185,129,0.15)'
                    : sentiment.classification.includes('Bearish')
                    ? 'rgba(239,68,68,0.15)'
                    : 'rgba(245,158,11,0.15)',
                color:
                  sentiment.classification.includes('Bullish')
                    ? '#10b981'
                    : sentiment.classification.includes('Bearish')
                    ? '#ef4444'
                    : '#f59e0b',
              }}
            >
              {sentiment.classification}
            </span>
          )}
        </div>

        {/* Sentiment gauge bar */}
        <div
          style={{
            width: '100%',
            height: '8px',
            borderRadius: '4px',
            background: 'linear-gradient(90deg, #ef4444 0%, #f59e0b 50%, #10b981 100%)',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          {sentiment && (
            <div
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                height: '100%',
                width: `${sentiment.sentiment_score * 100}%`,
                background: 'rgba(255,255,255,0.25)',
                borderRadius: '4px',
                transition: 'width 0.6s ease',
              }}
            />
          )}
          {/* Indicator marker */}
          {sentiment && (
            <div
              style={{
                position: 'absolute',
                top: '-3px',
                left: `calc(${sentiment.sentiment_score * 100}% - 6px)`,
                width: '14px',
                height: '14px',
                borderRadius: '50%',
                background: '#fff',
                border: '2px solid #8b5cf6',
                boxShadow: '0 0 6px rgba(139,92,246,0.6)',
                transition: 'left 0.6s ease',
                zIndex: 2,
              }}
            />
          )}
        </div>

        {/* Labels row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px' }}>
          <span style={{ fontSize: '0.65rem', color: '#ef4444', fontWeight: 600 }}>
            ← Distribution
          </span>
          {sentiment ? (
            <span style={{ fontSize: '0.7rem', color: '#8b8f98' }}>
              {sentiment.tx_count} txns · In: ${sentiment.inflow_usd.toLocaleString()} · Out: ${sentiment.outflow_usd.toLocaleString()}
            </span>
          ) : (
            <span style={{ fontSize: '0.7rem', color: '#8b8f98' }}>Loading sentiment…</span>
          )}
          <span style={{ fontSize: '0.65rem', color: '#10b981', fontWeight: 600 }}>
            Accumulation →
          </span>
        </div>
      </div>

      {/* Stats bar */}
      <div className="stats-bar">
        <div className="stat-card">
          <div className="stat-label">Portfolio Value</div>
          <div className="stat-value">{fmtTotal(displayTotal, currency)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">My Wallets</div>
          <div className="stat-value">{portfolio.wallets_tracked}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Whale Wallets</div>
          <div className="stat-value" style={{ color: '#8b5cf6' }}>{portfolio.whale_wallets_tracked}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Active Alerts</div>
          <div className="stat-value" style={{ color: '#f59e0b' }}>
            {alerts.filter(a => a.enabled).length}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        {/* Personal Wallets — only these count toward portfolio total */}
        <div className="card">
          <div className="card-title">◎ My Wallets</div>
          {!personal_wallets || personal_wallets.length === 0 ? (
            <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
              No personal wallets yet. Add one in the Wallets tab.
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
                  {personal_wallets.slice(0, 5).map(w => (
                    <tr key={w.id}>
                      <td><ChainBadge chain={w.chain} showLabel={false} /></td>
                      <td>{w.label || '—'}</td>
                      <td className="address">{truncateAddress(w.address)}</td>
                      <td>
                        {fmtBalance(w, currency)}
                        {isLiveUpdate(w.last_balance_update) && (
                          <span
                            title={`Updated ${timeAgo(w.last_balance_update)}`}
                            style={{
                              display: 'inline-block',
                              width: '6px',
                              height: '6px',
                              borderRadius: '50%',
                              backgroundColor: '#10b981',
                              marginLeft: '6px',
                              verticalAlign: 'middle',
                              boxShadow: '0 0 4px rgba(16,185,129,0.6)',
                            }}
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Whale Wallets — tracked for monitoring, NOT counted in portfolio total */}
        <div className="card">
          <div className="card-title">🐋 Whale Tracker <span style={{ color: '#8b8f98', fontSize: '0.7rem', fontWeight: 400 }}>(not in portfolio)</span></div>
          {!whale_wallets_list || whale_wallets_list.length === 0 ? (
            <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
              No whale wallets tracked. Add whale addresses from the Wallets tab.
            </p>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Chain</th>
                    <th>Label</th>
                    <th>Address</th>
                    <th>Balance</th>
                    <th>Score</th>
                  </tr>
                </thead>
                <tbody>
                  {whale_wallets_list.slice(0, 5).map(w => (
                    <tr key={w.id}>
                      <td><ChainBadge chain={w.chain} showLabel={false} /></td>
                      <td>
                        {w.label || '—'}
                        <RiskBadge wallet={w} />
                      </td>
                      <td className="address">{truncateAddress(w.address)}</td>
                      <td>
                        {fmtBalance(w, currency)}
                        {isLiveUpdate(w.last_balance_update) && (
                          <span
                            title={`Updated ${timeAgo(w.last_balance_update)}`}
                            style={{
                              display: 'inline-block',
                              width: '6px',
                              height: '6px',
                              borderRadius: '50%',
                              backgroundColor: '#10b981',
                              marginLeft: '6px',
                              verticalAlign: 'middle',
                              boxShadow: '0 0 4px rgba(16,185,129,0.6)',
                            }}
                          />
                        )}
                      </td>
                      <td>
                        {w.whale_score != null && w.whale_score > 0 ? (
                          <span style={{
                            fontSize: '0.7rem',
                            fontWeight: 600,
                            padding: '1px 6px',
                            borderRadius: '3px',
                            background: w.whale_score >= 0.7 ? 'rgba(139,92,246,0.2)' : 'rgba(139,143,152,0.15)',
                            color: w.whale_score >= 0.7 ? '#c4b5fd' : '#8b8f98',
                          }}>
                            {(w.whale_score * 100).toFixed(0)}%
                          </span>
                        ) : (
                          <span style={{ color: '#8b8f98', fontSize: '0.75rem' }}>—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

      </div>

      {/* Copy Trade Signals — full-width below the wallet grid */}
      <div className="card" style={{ marginTop: '20px' }}>
          <div className="card-title">◑ Copy Trade Signals</div>
          {copy_trade_signals.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '32px 24px' }}>
              <p style={{ fontWeight: 600, color: '#c4b5fd', marginBottom: '6px', fontSize: '14px' }}>No whale signals yet</p>
              <p style={{ fontSize: '13px', color: '#8b8f98', lineHeight: 1.6, maxWidth: '300px', margin: '0 auto 14px' }}>
                Add whale wallets in the <strong>Wallets</strong> tab. When they make a significant buy, signals appear here automatically.
              </p>
              <button className="btn btn-secondary btn-sm" onClick={() => { window.location.hash = '#wallets'; }}>
                Go to Wallets →
              </button>
            </div>
          ) : (
            copy_trade_signals.map(s => (
              <div key={s.id} className="signal-card">
                <div className="signal-info" style={{ flex: 1, minWidth: 0 }}>
                  <div className="signal-token">{s.token_symbol} {s.action.toUpperCase()}</div>
                  <div className="signal-meta">
                    {s.wallet_label} · {fmtTotal(s.amount_usd, currency)} · {timeAgo(s.created_at)}
                  </div>
                  {s.wallet_address && (
                    <div style={{ fontSize: '11px', color: '#6b7280', fontFamily: 'monospace', marginTop: '2px' }}>
                      {s.wallet_address.slice(0, 6)}...{s.wallet_address.slice(-4)}
                    </div>
                  )}
                  {s.explanation && (
                    <div style={{
                      fontSize: '12px', color: '#8b8f98', marginTop: '4px', lineHeight: 1.4,
                      display: 'flex', alignItems: 'center', gap: '6px',
                    }}>
                      <span>{s.explanation}</span>
                      {s.explanation_stale && (
                        <button
                          className="btn btn-secondary btn-sm"
                          style={{ fontSize: '10px', padding: '1px 4px', lineHeight: 1 }}
                          onClick={async (e) => {
                            e.stopPropagation();
                            try {
                              const res = await apiFetch(`/signals/${s.id}/explain`, token, { method: 'POST' });
                              s.explanation = res.explanation;
                              s.explanation_stale = false;
                              setData(prev => ({ ...prev }));
                            } catch (err) { /* silent */ }
                          }}
                          title="Regenerate explanation"
                        >↻</button>
                      )}
                    </div>
                  )}
                </div>
                <span className="signal-confidence" style={{
                  color: s.confidence_score > 0.7 ? '#10b981' : '#f59e0b'
                }}>
                  {(s.confidence_score * 100).toFixed(0)}%
                </span>
                {s.confidence_final != null && (
                  <span style={{
                    fontSize: '0.7rem', color: '#8b8f98', padding: '1px 5px',
                    border: '1px solid rgba(139,143,152,0.2)', borderRadius: '3px',
                  }}>
                    final: {(s.confidence_final * 100).toFixed(0)}%
                  </span>
                )}
                <button
                  className="btn btn-success btn-sm"
                  onClick={() => {
                    if (mirroringIds.has(s.id)) return;  // Finding: guard against double-click
                    if (window.confirm(`Mirror trade: BUY ${s.token_symbol} for ~${fmtTotal(s.amount_usd, currency)} via Alpaca paper trading. Continue?`)) {
                      setMirroringIds(prev => new Set(prev).add(s.id));
                      apiFetch(`/signals/${s.id}/mirror`, token, { method: 'POST' })
                        .then(load)
                        .catch(e => alert(`Failed: ${e.message}`))
                        .finally(() => setMirroringIds(prev => { const n = new Set(prev); n.delete(s.id); return n; }));
                    }
                  }}
                  disabled={s.status === 'executed' || mirroringIds.has(s.id)}
                >
                  {s.status === 'executed' ? 'Done' : mirroringIds.has(s.id) ? 'Mirroring...' : 'Mirror'}
                </button>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => setAnalyzeSignal(s)}
                >
                  Analyze
                </button>
              </div>
            ))
          )}
        </div>

      {/* Recent Activity — Live Transaction Feed */}
      <div className="card">
        <div className="card-title">
          ◎ Recent Activity
          <span style={{ float: 'right', color: '#8b8f98', fontSize: '0.75rem', fontWeight: 400 }}>
            {recent_transactions.length > 0 ? `Live · ${recent_transactions.length} txns` : 'No activity yet'}
          </span>
        </div>
        {recent_transactions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '32px 24px' }}>
            <p style={{ fontWeight: 600, color: '#c4b5fd', marginBottom: '6px', fontSize: '14px' }}>Waiting for transactions</p>
            <p style={{ fontSize: '13px', color: '#8b8f98', lineHeight: 1.6, maxWidth: '300px', margin: '0 auto 14px' }}>
              The monitor polls every 60s. Add a wallet in the <strong>Wallets</strong> tab and transactions will stream in automatically.
            </p>
            <button className="btn btn-secondary btn-sm" onClick={() => { window.location.hash = '#wallets'; }}>
              Add a wallet →
            </button>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Type</th>
                  <th>Chain</th>
                  <th>Wallet</th>
                  <th>Amount</th>
                  <th>Value</th>
                  <th>Tx Hash</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {recent_transactions.slice(0, 10).map((t, idx) => (
                  <tr key={t.id || t.tx_hash || idx}>
                    <td>
                      <span
                        className="status-indicator"
                        style={{
                          display: 'inline-block',
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          backgroundColor:
                            t.status === 'confirmed' ? '#10b981' :
                            t.status === 'pending' ? '#f59e0b' :
                            t.status === 'failed' ? '#ef4444' : '#8b8f98',
                          marginRight: '4px',
                          verticalAlign: 'middle',
                        }}
                        title={t.status || 'unknown'}
                      />
                      <span style={{ fontSize: '0.7rem', color: '#8b8f98', textTransform: 'capitalize' }}>
                        {t.status || '—'}
                      </span>
                    </td>
                    <td>
                      <span className={`tx-badge ${t.type}`}>{t.type}</span>
                    </td>
                    <td><ChainBadge chain={t.chain} showLabel={false} /></td>
                    <td>{t.wallet_label || truncateAddress(t.wallet_address)}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
                      {t.amount} {t.token}
                    </td>
                    <td>{fmtTotal(t.usd_value, currency)}</td>
                    <td className="address" style={{ fontSize: '0.75rem' }}>
                      {t.tx_hash ? truncateAddress(t.tx_hash) : '—'}
                    </td>
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
                    <td>{fmtTotal(a.threshold, currency)}</td>
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

      <DashboardAnalyzeModal
        signal={analyzeSignal}
        currency={currency}
        onMirror={(id, symbol, amount) => {
          if (mirroringIds.has(id)) return;  // Finding: guard against double-click
          if (window.confirm(`Mirror trade: BUY ${symbol} for ~${fmtTotal(amount, currency)} via Alpaca paper trading. Continue?`)) {
            setMirroringIds(prev => new Set(prev).add(id));
            apiFetch(`/signals/${id}/mirror`, token, { method: 'POST' })
              .then(load)
              .catch(e => alert(`Failed: ${e.message}`))
              .finally(() => setMirroringIds(prev => { const n = new Set(prev); n.delete(id); return n; }));
          }
        }}
        onClose={() => setAnalyzeSignal(null)}
      />
    </div>
  );
}

export default Dashboard;
