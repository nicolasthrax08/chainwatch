import React, { useState, useEffect, useCallback } from 'react';
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
};

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
                ? (STATUS_COLORS[signal.status] || '#8b8f98')
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

function CopyTrades({ token, currency }) {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);  // Finding: no error state, shows empty on failure
  const [mirroring, setMirroring] = useState(null);
  const [analyzeSignal, setAnalyzeSignal] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await apiFetch('/signals', token);
      setSignals(data.signals || []);
    } catch (e) {
      setError(e.message || 'Failed to load signals');  // Finding: was only console.error
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

  // Finding: error display with retry button
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
                  <span className="signal-confidence" style={{
                    color: s.confidence_score > 0.7 ? '#10b981' : '#f59e0b', fontSize: '0.9rem',
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
