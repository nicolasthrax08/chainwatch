import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { ChainBadge } from '../App';
import { ConfidenceBadge } from '../components/ConfidenceBadge';
import {
  apiFetch, timeAgo, fmtTotal, fmtBalance, truncateAddress, fmtDuration, STATUS_COLORS,
} from '../api';
import { BalanceSourceIndicator } from '../components/BalanceSourceIndicator';

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

/**
 * Compact signal history widget for the Dashboard.
 * Shows the 5 most recent closed signals with a summary header.
 */
function DashboardSignalHistory({ token, currency }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await apiFetch('/signals/history?limit=5', token);
      setHistory(data.signals || []);
    } catch (e) {
      setError(e.message || 'Failed to load signal history');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, [load]);

  const summary = useMemo(() => {
    const counts = { executed: 0, failed: 0, stale: 0 };
    for (const s of history) {
      if (counts[s.status] !== undefined) counts[s.status]++;
    }
    return { total: history.length, ...counts };
  }, [history]);

  return (
    <div className="card">
      <div
        className="card-title"
        style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
        onClick={() => setExpanded(e => !e)}
      >
        <span>
          Signal History
          {!loading && !error && history.length > 0 && (
            <span style={{ fontSize: '0.75rem', color: '#6b7280', fontWeight: 400, marginLeft: '8px' }}>
              {summary.total} closed
            </span>
          )}
        </span>
        <span style={{ fontSize: '0.75rem', color: '#8b8f98' }}>{expanded ? '▾' : '▸'}</span>
      </div>

      {!expanded && summary.total > 0 && (
        <div style={{ display: 'flex', gap: '16px', padding: '8px 0', fontSize: '0.8rem', color: '#8b8f98' }}>
          <span style={{ color: '#10b981' }}>✓ {summary.executed}</span>
          <span style={{ color: '#ef4444' }}>✗ {summary.failed}</span>
          <span style={{ color: '#6b7280' }}>⊘ {summary.stale}</span>
        </div>
      )}

      {expanded && (
        <div style={{ marginTop: '12px' }}>
          {loading && <div style={{ color: '#8b8f98', fontSize: '0.85rem', padding: '16px 0' }}>Loading history…</div>}

          {error && (
            <div style={{ color: '#ef4444', fontSize: '0.85rem', padding: '16px 0' }}>
              ⚠️ {error}
              <button className="btn btn-secondary btn-sm" style={{ marginLeft: '8px' }} onClick={() => { setLoading(true); load(); }}>
                Retry
              </button>
            </div>
          )}

          {!loading && !error && history.length === 0 && (
            <div style={{ textAlign: 'center', padding: '24px', color: '#6b7280', fontSize: '0.85rem' }}>
              No closed signals yet. Signals appear here after execution, failure, or expiry.
            </div>
          )}

          {!loading && !error && history.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', color: '#6b7280', textAlign: 'left' }}>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Token</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Action</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Amount</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Status</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Time to Close</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Closed</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map(s => (
                    <tr key={s.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600, color: '#e2e8f0' }}>{s.token_symbol}</td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{
                          fontSize: '0.7rem', fontWeight: 600, padding: '1px 5px', borderRadius: '3px',
                          background: s.action === 'buy' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                          color: s.action === 'buy' ? '#10b981' : '#ef4444',
                        }}>{s.action.toUpperCase()}</span>
                      </td>
                      <td style={{ padding: '6px 8px', color: '#c4b5fd' }}>{fmtTotal(s.amount_usd, currency)}</td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{
                          fontSize: '0.7rem', fontWeight: 600, padding: '1px 5px', borderRadius: '3px',
                          background: `${STATUS_COLORS[s.status] || '#6b7280'}22`,
                          color: STATUS_COLORS[s.status] || '#6b7280',
                        }}>{s.status}</span>
                      </td>
                      <td style={{ padding: '6px 8px', color: '#8b8f98' }}>{fmtDuration(s.time_to_close_seconds)}</td>
                      <td style={{ padding: '6px 8px', color: '#6b7280', fontSize: '0.7rem' }}>{timeAgo(s.closed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Signal performance stats widget for the Dashboard.
 * Shows aggregate signal statistics in a compact grid.
 * Reuses the same /api/signals/stats endpoint as CopyTrades.
 */
function DashboardSignalStats({ token }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch('/signals/stats', token);
        if (!cancelled) setStats(data);
      } catch (e) {
        // Silently fail — stats are optional enrichment
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [token]);

  if (loading || !stats || stats.total_signals === 0) return null;

  const executionRatePct = (stats.execution_rate * 100).toFixed(1);
  const avgConfPct = (stats.avg_confidence * 100).toFixed(0);
  const avgWhaleScorePct = (stats.avg_whale_score * 100).toFixed(0);

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
      gap: '12px',
      marginBottom: '20px',
    }}>
      {[
        { label: 'Total Signals', value: stats.total_signals, color: '#8b5cf6' },
        { label: 'Execution Rate', value: `${executionRatePct}%`, color: '#10b981' },
        { label: 'Avg Confidence', value: `${avgConfPct}%`, color: '#f59e0b' },
        { label: 'Avg Whale Score', value: `${avgWhaleScorePct}%`, color: '#c4b5fd' },
        { label: 'Pending', value: stats.by_status.pending, color: '#f59e0b' },
        { label: 'Executed', value: stats.by_status.executed, color: '#10b981' },
        { label: 'Signals (7d)', value: stats.recent_signals.last_7d, color: '#6b7280' },
      ].map(({ label, value, color }) => (
        <div key={label} style={{
          background: '#13141a',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: '8px',
          padding: '12px 14px',
        }}>
          <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '4px' }}>{label}</div>
          <div style={{ fontSize: '18px', fontWeight: 700, color }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

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
          ['Confidence', <ConfidenceBadge score={signal.confidence_score} />],
          ['Final Confidence', <ConfidenceBadge score={signal.confidence_final} />],
          ['Whale Score (at signal)', signal.score_at_generation?.toFixed(2) ?? '—'],
          ['Status', signal.status],
          ['Detected', timeAgo(signal.created_at)],
        ].map(([label, value]) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.05)', fontSize: '13px', alignItems: 'center' }}>
            <span style={{ color: '#8b8f98' }}>{label}</span>
            {React.isValidElement(value) ? value : (
              <span style={{
                color: label === 'Status'
                  ? (STATUS_COLORS[signal.status] || '#8b8f98')
                  : '#e2e8f0',
                fontWeight: 400,
              }}>{value}</span>
            )}
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

function SentimentHistory({ token }) {
  const [sentimentHistory, setSentimentHistory] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch('/whale-sentiment/history', token);
        if (!cancelled) setSentimentHistory(data);
      } catch {
        // Silently fail — sentiment history is non-critical
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 120000); // refresh every 2 min
    return () => { cancelled = true; clearInterval(interval); };
  }, [token]);

  if (loading || !sentimentHistory || !sentimentHistory.history) return null;

  const history = sentimentHistory.history;
  const maxTxCount = Math.max(...history.map(d => d.tx_count), 1);

  // Compute 7-day moving average for trend line
  const ma7 = history.map((d, i) => {
    const window = history.slice(Math.max(0, i - 6), i + 1);
    const avg = window.reduce((s, x) => s + x.sentiment_score, 0) / window.length;
    return { date: d.date, score: avg };
  });

  // Determine overall trend direction
  const firstWeek = ma7.slice(0, 7);
  const lastWeek = ma7.slice(-7);
  const firstAvg = firstWeek.reduce((s, x) => s + x.score, 0) / firstWeek.length;
  const lastAvg = lastWeek.reduce((s, x) => s + x.score, 0) / lastWeek.length;
  const trendDir = lastAvg > firstAvg + 0.02 ? 'up' : lastAvg < firstAvg - 0.02 ? 'down' : 'flat';
  const trendColor = trendDir === 'up' ? '#10b981' : trendDir === 'down' ? '#ef4444' : '#f59e0b';
  const trendLabel = trendDir === 'up' ? 'Trending Bullish' : trendDir === 'down' ? 'Trending Bearish' : 'Sideways';

  return (
    <div
      className="card"
      style={{
        marginBottom: '20px',
        padding: '16px 20px',
        border: '1px solid rgba(139,92,246,0.15)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '0.9rem' }}>📈</span>
          <span style={{ fontWeight: 700, fontSize: '0.8rem', color: '#c4b5fd', letterSpacing: '0.5px' }}>
            30-DAY SENTIMENT TREND
          </span>
        </div>
        <span style={{
          fontSize: '0.7rem',
          fontWeight: 600,
          color: trendColor,
          background: trendColor + '18',
          padding: '2px 8px',
          borderRadius: '6px',
        }}>
          {trendLabel}
        </span>
      </div>

      {/* Sparkline bar chart */}
      <div style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: '2px',
        height: '64px',
        marginBottom: '8px',
      }}>
        {history.map((d, i) => {
          const height = d.tx_count > 0
            ? Math.max(4, (d.tx_count / maxTxCount) * 100)
            : 3;
          const score = d.sentiment_score;
          // Color based on sentiment: red (0) → amber (0.5) → green (1)
          const barColor = score > 0.6 ? '#10b981'
            : score > 0.4 ? '#f59e0b'
            : '#ef4444';
          return (
            <div
              key={d.date}
              title={`${d.date}: ${(score * 100).toFixed(0)}% (${d.tx_count} txns)`}
              style={{
                flex: 1,
                height: `${height}%`,
                background: barColor,
                borderRadius: '2px 2px 0 0',
                opacity: d.tx_count > 0 ? 0.8 : 0.2,
                minWidth: '2px',
                cursor: 'default',
              }}
            />
          );
        })}
      </div>

      {/* Moving average line as SVG overlay info */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#8b8f98' }}>
        <span>30 days ago</span>
        <span style={{ color: '#6b7280' }}>
          {history.filter(d => d.tx_count > 0).reduce((s, d) => s + d.tx_count, 0)} total txns
        </span>
        <span>Today</span>
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

      {/* Sentiment Trend Chart */}
      <SentimentHistory token={token} />

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

      {/* Signal Performance Stats — shown when user has signals */}
      <DashboardSignalStats token={token} />

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
                        <BalanceSourceIndicator wallet={w} />
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
                        <BalanceSourceIndicator wallet={w} />
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
                <ConfidenceBadge score={s.confidence_score} />
                {s.confidence_final != null && (
                  <ConfidenceBadge score={s.confidence_final} label="final" />
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

      {/* Signal History — Closed Signals */}
      <DashboardSignalHistory token={token} currency={currency} />

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
