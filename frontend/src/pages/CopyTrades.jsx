import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { API_BASE } from '../config';
import { ChainBadge } from '../App';
import { ConfidenceBadge } from '../components/ConfidenceBadge';

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
  if (!timestamp) return '--';
  const diffMs = Date.now() - new Date(timestamp).getTime();
  if (diffMs <= 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function fmtTotal(value, currency) {
  if (!value && value !== 0) return '--';
  if (currency === 'BTC') return `₿${value.toFixed(8)}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

const STATUS_COLORS = {
  pending: '#f59e0b',
  executed: '#10b981',
  failed: '#ef4444',
  stale: '#6b7280',
};

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function fmtDuration(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

function AnalyzeModal({ signal, currency, onMirror, onClose }) {
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
          ['Address', signal.wallet_address ? truncateAddress(signal.wallet_address) : '—'],
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

function SignalsEmptyState() {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px' }}>
      <div style={{
        width: '40px', height: '40px', borderRadius: '50%',
        background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        margin: '0 auto 12px', fontSize: '18px',
      }}>🐋</div>
      <p style={{ fontWeight: 600, color: '#c4b5fd', marginBottom: '8px', fontSize: '14px' }}>No whale signals yet</p>
      <p style={{ fontSize: '13px', color: '#8b8f98', lineHeight: 1.6, maxWidth: '320px', margin: '0 auto 20px' }}>
        Signals generate when a tracked whale wallet makes a significant buy.
        You need at least one whale wallet being monitored.
      </p>
      <div style={{ maxWidth: '300px', margin: '0 auto 20px', textAlign: 'left' }}>
        {[
          ['1', <>Add a whale address from <strong>Wallets → Whale Suggestions</strong></>],
          ['2', 'The monitor checks every 60s for significant buys'],
          ['3', 'A signal appears here with a confidence score'],
        ].map(([n, text]) => (
          <div key={n} style={{ display: 'flex', gap: '10px', marginBottom: '10px', alignItems: 'flex-start' }}>
            <span style={{
              width: '20px', height: '20px', borderRadius: '50%', flexShrink: 0,
              background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.3)',
              fontSize: '11px', color: '#c4b5fd', display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>{n}</span>
            <span style={{ fontSize: '13px', color: '#8b8f98', lineHeight: 1.5 }}>{text}</span>
          </div>
        ))}
      </div>
      <button
        className="btn btn-secondary btn-sm"
        onClick={() => { window.location.hash = '#wallets'; }}
      >
        Browse whale suggestions →
      </button>
    </div>
  );
}

function SignalStats({ token }) {
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

      {/* ── Whale Tier Performance ── */}
      {stats.performance_by_tier && Object.keys(stats.performance_by_tier).length > 0 && (
        <>
          <div style={{
            gridColumn: '1 / -1',
            marginTop: '8px',
            marginBottom: '4px',
            fontSize: '12px',
            fontWeight: 600,
            color: '#8b8f98',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
          }}>
            Performance by Whale Tier
          </div>
          {(['high', 'medium', 'low'] as const).map((tier) => {
            const tierData = stats.performance_by_tier[tier];
            if (!tierData) return null;
            const tierExecPct = (tierData.execution_rate * 100).toFixed(1);
            const tierConfPct = (tierData.avg_confidence * 100).toFixed(0);
            const tierColor = tier === 'high' ? '#10b981' : tier === 'medium' ? '#f59e0b' : '#ef4444';
            const tierLabel = tier === 'high' ? 'High Whale (≥0.7)' : tier === 'medium' ? 'Medium (0.4–0.7)' : 'Low (<0.4)';
            return [
              <div key={`${tier}-label`} style={{
                background: '#13141a',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: '8px',
                padding: '12px 14px',
                borderLeft: `3px solid ${tierColor}`,
              }}>
                <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '4px' }}>{tierLabel}</div>
                <div style={{ fontSize: '13px', color: '#d1d5db' }}>
                  {tierData.total} signals · {tierData.executed} executed
                </div>
              </div>,
              <div key={`${tier}-exec`} style={{
                background: '#13141a',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: '8px',
                padding: '12px 14px',
              }}>
                <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '4px' }}>Exec. Rate</div>
                <div style={{ fontSize: '18px', fontWeight: 700, color: tierColor }}>{tierExecPct}%</div>
              </div>,
              <div key={`${tier}-conf`} style={{
                background: '#13141a',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: '8px',
                padding: '12px 14px',
              }}>
                <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '4px' }}>Avg Confidence</div>
                <div style={{ fontSize: '18px', fontWeight: 700, color: '#f59e0b' }}>{tierConfPct}%</div>
              </div>,
            ];
          })}
        </>
      )}
    </div>
  );
}

function SignalHistory({ token, currency }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (filter !== 'all') params.set('status_filter', filter);
      const data = await apiFetch(`/signals/history?${params}`, token);
      setHistory(data.signals || []);
    } catch (e) {
      setError(e.message || 'Failed to load signal history');
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, [load]);

  const summary = useMemo(() => {
    const counts = { executed: 0, failed: 0, stale: 0 };
    let totalTtc = 0;
    let ttcCount = 0;
    for (const s of history) {
      if (counts[s.status] !== undefined) counts[s.status]++;
      if (s.time_to_close_seconds > 0) {
        totalTtc += s.time_to_close_seconds;
        ttcCount++;
      }
    }
    return {
      total: history.length,
      ...counts,
      avgTtc: ttcCount > 0 ? totalTtc / ttcCount : 0,
    };
  }, [history]);

  return (
    <div className="card" style={{ marginTop: '16px' }}>
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
          <span style={{ color: '#10b981' }}>✓ {summary.executed} executed</span>
          <span style={{ color: '#ef4444' }}>✗ {summary.failed} failed</span>
          <span style={{ color: '#6b7280' }}>⊘ {summary.stale} stale</span>
          {summary.avgTtc > 0 && <span>⏱ avg {fmtDuration(summary.avgTtc)}</span>}
        </div>
      )}

      {expanded && (
        <div style={{ marginTop: '12px' }}>
          <div style={{ display: 'flex', gap: '4px', marginBottom: '12px' }}>
            {['all', 'executed', 'failed', 'stale'].map(f => (
              <button
                key={f}
                className={`btn btn-sm ${filter === f ? 'btn-primary' : 'btn-secondary'}`}
                style={{ fontSize: '0.75rem', padding: '3px 10px', textTransform: 'capitalize' }}
                onClick={() => { setFilter(f); setLoading(true); }}
              >
                {f === 'all' ? `All (${summary.total})` : `${f} (${summary[f] || 0})`}
              </button>
            ))}
          </div>

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
              No closed signals yet. Signals will appear here after they are executed, marked as failed, or expire.
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
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Whale</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Conf.</th>
                    <th style={{ padding: '6px 8px', fontWeight: 500 }}>Final</th>
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
                      <td style={{ padding: '6px 8px', color: '#8b8f98', maxWidth: '100px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={s.wallet_address}>
                        {s.wallet_label || truncateAddress(s.wallet_address)}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <ConfidenceBadge score={s.confidence_score} size="sm" />
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        {s.confidence_final != null
                          ? <ConfidenceBadge score={s.confidence_final} size="sm" />
                          : <span style={{ color: '#6b7280' }}>—</span>}
                      </td>
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

function CopyTrades({ token, currency }) {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [mirroring, setMirroring] = useState(null);
  const [analyzeSignal, setAnalyzeSignal] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await apiFetch('/signals', token);
      setSignals(data.signals || []);
    } catch (e) {
      setError(e.message || 'Failed to load signals');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, [load]);

  const handleMirror = async (signalId, tokenSymbol, amountUsd) => {
    const confirmed = window.confirm(
      `Mirror trade: BUY ${tokenSymbol} for ~${fmtTotal(amountUsd, currency)} via Alpaca paper trading.\n\nThis places a real paper trade order. Continue?`
    );
    if (!confirmed) return;
    setMirroring(signalId);
    try {
      await apiFetch(`/signals/${signalId}/mirror`, token, { method: 'POST' });
      load();
    } catch (e) {
      alert(`Mirror trade failed: ${e.message}`);
    } finally {
      setMirroring(null);
    }
  };

  if (loading) return <div className="loading">Loading signals</div>;

  if (error) return (
    <div style={{ textAlign: 'center', padding: '40px', color: '#ef4444' }}>
      <p>⚠️ {error}</p>
      <button className="btn btn-primary" onClick={() => { setLoading(true); load(); }} style={{ marginTop: '12px' }}>
        Retry
      </button>
    </div>
  );

  return (
    <div>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '24px', color: '#8b5cf6' }}>◑ Copy Trade Signals</h1>

      {/* Signal Performance Stats Widget */}
      <SignalStats token={token} />

      <div className="card">
        <div className="card-title">
          Whale trade signals
          <span style={{ float: 'right', color: '#8b8f98', fontSize: '0.75rem', fontWeight: 400 }}>
            Alpaca paper trading
          </span>
        </div>

        {signals.length === 0 ? <SignalsEmptyState /> : (
          <div>
            {signals.map(s => (
              <div key={s.id} className="signal-card" style={{
                borderLeft: `2px solid ${STATUS_COLORS[s.status] || '#2a2e38'}`,
                borderRadius: '0 8px 8px 0',
              }}>
                <div className="signal-info" style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
                    <span className="signal-token">{s.token_symbol}</span>
                    <span className={`tx-badge ${s.action}`}>{s.action}</span>
                    <span style={{
                      fontSize: '10px', padding: '1px 6px', borderRadius: '3px', fontWeight: 600,
                      background: `${STATUS_COLORS[s.status] || '#888'}22`,
                      color: STATUS_COLORS[s.status] || '#8b8f98',
                    }}>{s.status}</span>
                  </div>
                  <div className="signal-meta">{s.wallet_label} · {fmtTotal(s.amount_usd, currency)} · {timeAgo(s.created_at)}</div>
                  {s.wallet_address && (
                    <div style={{ fontSize: '11px', color: '#6b7280', fontFamily: 'monospace', marginTop: '2px' }}>
                      {truncateAddress(s.wallet_address)}
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
                              setSignals(prev => [...prev]);
                            } catch (err) { /* silent */ }
                          }}
                          title="Regenerate explanation"
                        >↻</button>
                      )}
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                  <ConfidenceBadge score={s.confidence_score} />
                  {s.confidence_final != null && (
                    <ConfidenceBadge score={s.confidence_final} label="final" />
                  )}
                  <button
                    className="btn btn-success btn-sm"
                    onClick={() => handleMirror(s.id, s.token_symbol, s.amount_usd)}
                    disabled={mirroring === s.id || s.status === 'executed'}
                  >
                    {mirroring === s.id ? '...' : s.status === 'executed' ? 'Done' : 'Mirror'}
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setAnalyzeSignal(s)}
                  >
                    Analyze
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Signal History (closed signals) */}
      <SignalHistory token={token} currency={currency} />

      <div className="card" style={{ background: 'rgba(139,92,246,0.05)', marginTop: '16px' }}>
        <div className="card-title">How copy trading works</div>
        <ul style={{ color: '#8b8f98', fontSize: '0.85rem', paddingLeft: '20px', lineHeight: 1.8 }}>
          <li>ChainWatch monitors whale wallets you track for significant trades</li>
          <li>When a whale buys a new token with significant volume, a signal is generated</li>
          <li>Click <strong>Mirror</strong> to execute the same trade via Alpaca paper trading</li>
          <li>Click <strong>Analyze</strong> to review signal details before acting</li>
          <li>Confidence score is based on trade volume relative to typical whale activity</li>
          <li>Final confidence blends the signal score with the whale's historical score at signal time</li>
          <li>All mirror trades use Alpaca paper trading — no real funds at risk</li>
        </ul>
      </div>

      <AnalyzeModal
        signal={analyzeSignal}
        currency={currency}
        onMirror={handleMirror}
        onClose={() => setAnalyzeSignal(null)}
      />
    </div>
  );
}

export default CopyTrades;
