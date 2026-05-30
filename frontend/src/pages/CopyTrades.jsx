import React, { useState, useEffect } from 'react';

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

function CopyTrades({ token }) {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [mirroring, setMirroring] = useState(null);

  const load = async () => {
    try {
      const data = await apiFetch('/signals', token);
      setSignals(data.signals || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, [token]);

  const handleMirror = async (signalId) => {
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

  const handleAnalyze = (signal) => {
    alert(`Analysis for ${signal.token_symbol}:\n\nWhale: ${signal.wallet_label}\nAction: ${signal.action}\nAmount: $${signal.amount_usd.toLocaleString()}\nConfidence: ${(signal.confidence_score * 100).toFixed(0)}%\n\nSignal detected at: ${signal.created_at}`);
  };

  if (loading) return <div className="loading">Loading signals</div>;

  const statusColors = {
    pending: '#f59e0b',
    executed: '#10b981',
    failed: '#ef4444',
  };

  return (
    <div>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '24px', color: '#8b5cf6' }}>
        ◑ Copy Trade Signals
      </h1>

      <div className="card">
        <div className="card-title">
          Whale Trade Signals
          <span style={{ float: 'right', color: '#8b8f98', fontSize: '0.75rem' }}>
            Powered by on-chain monitoring · Alpaca Paper Trading
          </span>
        </div>
        {signals.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#8b8f98' }}>
            <p>No copy trade signals yet.</p>
            <p style={{ fontSize: '0.85rem', marginTop: '8px' }}>
              Signals are generated when tracked whale wallets make significant trades.
            </p>
          </div>
        ) : (
          <div>
            {signals.map(s => (
              <div key={s.id} className="signal-card" style={{
                borderLeft: `3px solid ${statusColors[s.status] || '#2a2e38'}`
              }}>
                <div className="signal-info">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className="signal-token">{s.token_symbol}</span>
                    <span className={`tx-badge ${s.action}`}>{s.action}</span>
                    <span style={{
                      fontSize: '0.7rem',
                      padding: '1px 6px',
                      borderRadius: '3px',
                      background: `${statusColors[s.status]}22`,
                      color: statusColors[s.status],
                      fontWeight: 'bold'
                    }}>
                      {s.status}
                    </span>
                  </div>
                  <div className="signal-meta">
                    Whale: {s.wallet_label} · ${s.amount_usd.toLocaleString()} · {timeAgo(s.created_at)}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span className="signal-confidence" style={{
                    color: s.confidence_score > 0.7 ? '#10b981' : '#f59e0b',
                    fontSize: '0.9rem'
                  }}>
                    {(s.confidence_score * 100).toFixed(0)}% conf
                  </span>
                  <button
                    className="btn btn-success btn-sm"
                    onClick={() => handleMirror(s.id)}
                    disabled={mirroring === s.id || s.status === 'executed'}
                  >
                    {mirroring === s.id ? '...' : s.status === 'executed' ? 'Done' : 'Mirror'}
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleAnalyze(s)}
                  >
                    Analyze
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Info card */}
      <div className="card" style={{ background: 'rgba(139, 92, 246, 0.05)' }}>
        <div className="card-title">ℹ How Copy Trading Works</div>
        <ul style={{ color: '#8b8f98', fontSize: '0.85rem', paddingLeft: '20px', lineHeight: 1.8 }}>
          <li>ChainWatch monitors whale wallets you track for significant trades</li>
          <li>When a whale buys a new token with significant volume, a signal is generated</li>
          <li>Click <strong>Mirror</strong> to execute the same trade via Alpaca paper trading</li>
          <li>Click <strong>Analyze</strong> to see more details about the signal</li>
          <li>Confidence score is based on trade volume relative to typical whale activity</li>
          <li>All mirror trades use Alpaca paper trading — no real funds at risk</li>
        </ul>
      </div>
    </div>
  );
}

// Fix the useEffect interval bug
// (The comma operator was a typo — should be [token])

export default CopyTrades;
